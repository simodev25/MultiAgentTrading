from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ToolHandler = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass(slots=True)
class RuntimeToolDefinition:
    name: str
    description: str
    section: str
    profiles: tuple[str, ...]
    handler: ToolHandler


class RuntimeToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RuntimeToolDefinition] = {}
        self._allow: set[str] | None = None
        self._deny: set[str] = set()

    def register(
        self,
        name: str,
        handler: ToolHandler,
        *,
        description: str = '',
        section: str = 'runtime',
        profiles: tuple[str, ...] = (),
    ) -> None:
        normalized = str(name or '').strip()
        if not normalized:
            raise ValueError('Tool name is required.')
        self._tools[normalized] = RuntimeToolDefinition(
            name=normalized,
            description=str(description or '').strip(),
            section=str(section or 'runtime').strip() or 'runtime',
            profiles=tuple(str(item).strip() for item in profiles if str(item).strip()),
            handler=handler,
        )
        if self._allow is not None:
            self._allow.add(normalized)

    def set_policy(
        self,
        *,
        allow: list[str] | None = None,
        deny: list[str] | None = None,
    ) -> None:
        normalized_allow = {str(item).strip() for item in (allow or []) if str(item).strip()}
        normalized_deny = {str(item).strip() for item in (deny or []) if str(item).strip()}
        self._allow = None if not normalized_allow or '*' in normalized_allow else normalized_allow
        self._deny = normalized_deny

    def has(self, name: str) -> bool:
        return str(name or '').strip() in self._tools

    def list_tools(self) -> list[dict[str, object]]:
        return [
            {
                'name': item.name,
                'description': item.description,
                'section': item.section,
                'profiles': list(item.profiles),
            }
            for item in self._tools.values()
        ]

    def _is_allowed(self, name: str) -> bool:
        if name in self._deny:
            return False
        if self._allow is None:
            return True
        return name in self._allow

    @staticmethod
    def _normalize_allowlist(allowed_tools: list[str] | tuple[str, ...] | set[str] | None) -> set[str] | None:
        if allowed_tools is None:
            return None
        return {
            str(item or '').strip()
            for item in allowed_tools
            if str(item or '').strip()
        }

    async def call(
        self,
        name: str,
        *,
        allowed_tools: list[str] | tuple[str, ...] | set[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        normalized = str(name or '').strip()
        definition = self._tools.get(normalized)
        if definition is None:
            raise KeyError(f'Unknown runtime tool: {normalized}')
        if not self._is_allowed(normalized):
            raise PermissionError(f'Runtime tool denied by policy: {normalized}')
        scoped_allow = self._normalize_allowlist(allowed_tools)
        if scoped_allow is not None and normalized not in scoped_allow:
            raise PermissionError(f'Runtime tool denied by scoped allowlist: {normalized}')

        result = definition.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            return result
        return {'value': result}
