from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.services.llm.model_selector import (
    DETERMINISTIC_ONLY_AGENTS,
    MAX_AGENT_SKILL_LENGTH,
    MAX_AGENT_SKILLS_PER_AGENT,
    normalize_agent_name,
)

BOOTSTRAP_META_KEY = 'agent_skills_bootstrap_meta'
_SKILL_SPLIT_RE = re.compile(r'[\n,]+')


def bootstrap_agent_skills_into_settings(
    current_settings: dict,
    bootstrap_file: str | None,
    mode: str = 'merge',
    apply_once: bool = True,
) -> tuple[dict, bool, str]:
    normalized_settings = dict(current_settings or {})
    source_file = str(bootstrap_file or '').strip()
    if not source_file:
        return normalized_settings, False, 'disabled'

    payload, error = _load_payload(source_file)
    if payload is None:
        return normalized_settings, False, f'load-failed:{error or "unknown"}'

    proposed_skills = extract_agent_skills_from_payload(payload)
    if not proposed_skills:
        return normalized_settings, False, 'no-skills-found'

    normalized_mode = 'replace' if str(mode or '').strip().lower() == 'replace' else 'merge'
    fingerprint = _compute_fingerprint(proposed_skills)
    existing_meta = normalized_settings.get(BOOTSTRAP_META_KEY)

    if apply_once and isinstance(existing_meta, dict):
        existing_fingerprint = str(existing_meta.get('fingerprint') or '').strip()
        existing_mode = str(existing_meta.get('mode') or '').strip().lower()
        if existing_fingerprint == fingerprint and existing_mode == normalized_mode:
            return normalized_settings, False, 'already-applied'

    current_agent_skills = _normalize_agent_skills_map(normalized_settings.get('agent_skills'))
    if normalized_mode == 'replace':
        next_agent_skills = proposed_skills
    else:
        next_agent_skills = _merge_agent_skill_maps(current_agent_skills, proposed_skills)

    next_meta = {
        'fingerprint': fingerprint,
        'mode': normalized_mode,
        'source_file': source_file,
    }
    if next_agent_skills == current_agent_skills and existing_meta == next_meta:
        return normalized_settings, False, 'no-op'

    updated_settings = dict(normalized_settings)
    updated_settings['agent_skills'] = next_agent_skills
    updated_settings[BOOTSTRAP_META_KEY] = next_meta
    return updated_settings, True, 'applied'


def extract_agent_skills_from_payload(payload: object) -> dict[str, list[str]]:
    if not isinstance(payload, dict):
        return {}

    direct = _normalize_agent_skills_map(payload.get('agent_skills'))
    if direct:
        return direct

    from_structured_map = _extract_from_structured_agent_skill_map(payload.get('agent_skill_map'))
    if from_structured_map:
        return from_structured_map

    return _extract_from_proposal_payload(payload)


def _extract_from_structured_agent_skill_map(raw_map: object) -> dict[str, list[str]]:
    if not isinstance(raw_map, list):
        return {}

    extracted: dict[str, list[str]] = {}
    for row in raw_map:
        if not isinstance(row, dict):
            continue

        agent_name = _clean_text(row.get('agent'))
        if not agent_name or agent_name in DETERMINISTIC_ONLY_AGENTS:
            continue

        raw_skills = row.get('proposed_skills')
        if isinstance(raw_skills, (list, tuple, set)):
            extracted[agent_name] = [str(item) for item in raw_skills]
        elif isinstance(raw_skills, str):
            extracted[agent_name] = [raw_skills]

    return _normalize_agent_skills_map(extracted)


def _extract_from_proposal_payload(payload: dict) -> dict[str, list[str]]:
    raw_skills = payload.get('skills')
    raw_mapping = payload.get('agent_mapping')
    if not isinstance(raw_skills, list) or not isinstance(raw_mapping, dict):
        return {}

    skill_text_by_id: dict[str, list[str]] = {}
    for row in raw_skills:
        if not isinstance(row, dict):
            continue
        skill_id = _clean_text(row.get('id'))
        if not skill_id:
            continue

        skill_name = _clean_text(row.get('skill_name')) or skill_id
        snippets: list[str] = []

        description = _clean_text(row.get('description'))
        if description:
            snippets.append(f'{skill_name}: {description}')

        evidence = row.get('evidence')
        if isinstance(evidence, dict):
            notable_points = evidence.get('notable_points')
            if isinstance(notable_points, (list, tuple, set)):
                for raw_point in notable_points:
                    point = _clean_text(raw_point)
                    if not point:
                        continue
                    snippets.append(f'{skill_name}: {point}')
                    if len(snippets) >= 3:
                        break

        if snippets:
            skill_text_by_id[skill_id] = snippets

    extracted: dict[str, list[str]] = {}
    for raw_agent_name, raw_agent_mapping in raw_mapping.items():
        agent_name = _clean_text(raw_agent_name)
        if not agent_name or agent_name in DETERMINISTIC_ONLY_AGENTS:
            continue
        if not isinstance(raw_agent_mapping, dict):
            continue

        snippets: list[str] = []
        for key in ('primary_skills', 'secondary_skills'):
            raw_skill_ids = raw_agent_mapping.get(key)
            if not isinstance(raw_skill_ids, (list, tuple, set)):
                continue
            for raw_skill_id in raw_skill_ids:
                skill_id = _clean_text(raw_skill_id)
                if not skill_id:
                    continue
                snippets.extend(skill_text_by_id.get(skill_id, []))

        notes = _clean_text(raw_agent_mapping.get('notes'))
        if notes:
            snippets.append(f'Contexte agent: {notes}')

        if snippets:
            extracted[agent_name] = snippets

    return _normalize_agent_skills_map(extracted)


def _merge_agent_skill_maps(current: dict[str, list[str]], incoming: dict[str, list[str]]) -> dict[str, list[str]]:
    merged = {agent_name: list(items) for agent_name, items in current.items()}
    for agent_name, new_items in incoming.items():
        existing_items = merged.get(agent_name, [])
        merged[agent_name] = _dedupe_skill_items([*existing_items, *new_items])
    return merged


def _normalize_agent_skills_map(raw_skills: object) -> dict[str, list[str]]:
    if not isinstance(raw_skills, dict):
        return {}

    normalized: dict[str, list[str]] = {}
    for raw_agent_name, raw_items in raw_skills.items():
        agent_name = normalize_agent_name(_clean_text(raw_agent_name))
        if not agent_name or agent_name in DETERMINISTIC_ONLY_AGENTS:
            continue

        items = _coerce_skill_items(raw_items)
        deduped = _dedupe_skill_items(items)
        if deduped:
            merged = list(normalized.get(agent_name, []))
            for item in deduped:
                if item in merged:
                    continue
                merged.append(item)
                if len(merged) >= MAX_AGENT_SKILLS_PER_AGENT:
                    break
            normalized[agent_name] = merged
    return normalized


def _coerce_skill_items(raw_items: object) -> list[str]:
    if isinstance(raw_items, str):
        return [part.strip() for part in _SKILL_SPLIT_RE.split(raw_items)]
    if isinstance(raw_items, (list, tuple, set)):
        return [str(item).strip() for item in raw_items]
    return []


def _dedupe_skill_items(raw_items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        cleaned = _clean_text(raw_item)
        if not cleaned:
            continue
        if len(cleaned) > MAX_AGENT_SKILL_LENGTH:
            cleaned = cleaned[:MAX_AGENT_SKILL_LENGTH].rstrip()
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
        if len(deduped) >= MAX_AGENT_SKILLS_PER_AGENT:
            break
    return deduped


def _clean_text(value: object) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    if not text:
        return ''
    return re.sub(r'\s+', ' ', text)


def _compute_fingerprint(agent_skills: dict[str, list[str]]) -> str:
    payload = json.dumps(agent_skills, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _load_payload(source_file: str) -> tuple[dict | None, str | None]:
    path = Path(source_file)
    if not path.exists():
        return None, 'file-not-found'
    if not path.is_file():
        return None, 'not-a-file'

    try:
        content = path.read_text(encoding='utf-8')
    except OSError as exc:
        return None, f'read-error:{exc}'

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        return None, f'invalid-json:{exc.msg}'

    if not isinstance(payload, dict):
        return None, 'invalid-root'
    return payload, None
