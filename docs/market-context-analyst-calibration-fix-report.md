# Market Context Analyst Calibration Fix Report

Date: 2026-03-20  
Project: `forex-market-context-analyst-calibration-fix-001`

## Scope du patch

Patch minimal centré sur:
- calibration interne de `market-context-analyst`,
- cohérence sémantique `reason` / `llm_summary`,
- tests ciblés.

Aucune refonte de la politique `permissive` du `trader-agent`.

## Fichiers modifiés (ce correctif)

- `backend/app/services/orchestrator/agents.py`
- `backend/tests/unit/test_market_context_agent.py`

## Problème précédent

Le comportement précédent pouvait produire un biais directionnel modéré avec:
- `momentum_bias = neutral`
- `volatility_context = neutral`

et verbaliser malgré tout un "soutien" directionnel de ces dimensions neutres.

C’était sémantiquement faux: une dimension neutre peut être non-invalidante, mais ne doit pas être décrite comme un renfort actif.

## Correction appliquée

### 1) Calibration score (trend hérité non confirmant)

Dans `market-context-analyst`, quand:
- `momentum_bias == neutral` et
- `volatility_context == neutral`

le score est désormais plafonné:
- en régime `calm/ranging`: clamp à `[-0.13, 0.13]`
- sinon: clamp à `[-0.17, 0.17]`

Objectif: éviter les biais trop agressifs en contexte tiède/non confirmant.

### 2) Calibration confidence

La confidence est maintenant plafonnée à `low` dans ce même cas neutre/neutral,
avec une exception explicite codée uniquement pour un cas de trend fort (`trending`, trend aligné, magnitude élevée) permettant au plus `medium`.

### 3) Génération de `reason`

La logique textuelle distingue désormais:
- soutien réel,
- héritage du trend,
- absence de confirmation,
- simple non-invalidation.

En particulier, la raison n’affirme plus que momentum/volatilité neutres "soutiennent" un biais.

### 4) Cohérence `llm_summary`

`llm_summary` reste dérivé de la sortie structurée finale (`signal`, `score`, `confidence`, `regime`, `momentum_bias`, `volatility_context`, `reason`) sans durcissement de ton.

## Compatibilité avec le mode permissive

Aucun changement de policy trader.

Le test `permissive_mode_can_still_trade_after_context_patch` valide qu’un `SELL` reste possible en mode permissive quand la politique trader le permet (technique fort + contexte faiblement baissier).

## Tests ajoutés/ajustés

Dans `test_market_context_agent.py`:
- `neutral_momentum_and_neutral_volatility_do_not_count_as_active_support`
- `weak_trend_inheritance_keeps_low_confidence`
- `llm_summary_matches_structured_context_output`
- `permissive_mode_can_still_trade_after_context_patch`

## Validation exécutée

- `pytest -q backend/tests/unit/test_market_context_agent.py backend/tests/unit/test_agent_runtime_skills.py backend/tests/unit/test_trader_agent.py`
- `pytest -q backend/tests/unit`

Résultat:
- `186 passed`, `0 failed`.

## Limites restantes

- Le calibrage reste heuristique (proxy marché), pas un modèle macro fondamental.
- Le wording est désormais cohérent et prudent, mais peut encore être affiné selon vos conventions de style internes.
