# Execution Manager v2 — Preflight Déterministe

**Date:** 2026-04-01
**Statut:** Terminé
**Objectif:** Remplacer le LLM execution-manager par un engine de vérification pré-vol déterministe (`ExecutionPreflightEngine`) qui valide toutes les conditions opérationnelles avant exécution. Le LLM reste optionnel (désactivé par défaut) pour résumé human-readable.
**Prerequis:** Risk Manager Niveau 2 (Tier 1+2+3) — terminé

---

## Contexte

### Situation actuelle

L'execution-manager est un agent LLM qui :
- Reçoit le résultat du trader-agent et du risk-manager en texte
- Décide `should_execute=true|false` via un appel LLM (~3-5s)
- N'a accès qu'à un seul tool (`market_snapshot`)
- Ne fait aucune vérification opérationnelle (spread, market hours, broker status, volume broker)
- Pour les HOLD, est bypassé complètement (réponse déterministe)

**Problèmes :**
- Le LLM n'apporte pas de valeur sur des checks binaires (marché ouvert/fermé, spread > seuil)
- Risque d'hallucination : le LLM peut transformer BUY en SELL, inventer des niveaux
- Aucune vérification de spread, heures de marché, disponibilité broker
- Coût inutile : 1 appel LLM pour répéter ce que le risk-manager a validé
- Les vraies validations broker (symbol, volume step) sont dans `MetaApiClient.place_order()` → trop tard pour un rejet propre

### Architecture cible

```
Trader → Risk-Manager → ExecutionPreflightEngine (déterministe) → ExecutionService.execute()
                                    ↓
                          LLM execution-manager (optionnel, désactivé par défaut)
```

---

## Statuts d'exécution unifiés

| Statut | Signification | Quand |
|--------|--------------|-------|
| `executed` | Ordre envoyé au broker avec succès | paper/live, broker OK |
| `simulated` | Ordre simulé (pas envoyé au broker) | mode simulation |
| `blocked` | Preflight a rejeté (raison opérationnelle) | spread excessif, marché fermé, broker down, volume invalide |
| `refused` | Risk-manager a rejeté le trade | `accepted=false` du risk-manager |
| `skipped` | Décision HOLD, rien à exécuter | trader dit HOLD |
| `failed` | Envoyé au broker mais erreur retournée | broker rejette l'ordre (funds, symbol, etc.) |

---

## Plan d'implémentation

### Tache 1 : ExecutionStatus enum

**Fichier:** `backend/app/services/execution/preflight.py` (nouveau)

```python
from enum import Enum

class ExecutionStatus(str, Enum):
    EXECUTED = "executed"
    SIMULATED = "simulated"
    BLOCKED = "blocked"
    REFUSED = "refused"
    SKIPPED = "skipped"
    FAILED = "failed"
```

**Statut:** [x] Terminé

---

### Tache 2 : ExecutionPreflightEngine

**Fichier:** `backend/app/services/execution/preflight.py` (nouveau)

Engine déterministe avec 8 checks séquentiels, reject au premier échec.

```python
@dataclass
class PreflightResult:
    status: ExecutionStatus
    can_execute: bool
    reason: str
    checks_passed: list[str]
    checks_failed: list[str]
    # Données validées (passées à l'executor si OK)
    side: str | None = None          # BUY | SELL
    volume: float = 0.0
    entry: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    symbol: str = ""
    mode: str = "simulation"

class ExecutionPreflightEngine:
    def validate(
        self,
        trader_output: dict,
        risk_output: dict,
        snapshot: dict,
        pair: str,
        mode: str,
    ) -> PreflightResult:
```

**Checks séquentiels :**

| # | Check | Source | Condition de blocage | Statut résultant |
|---|-------|--------|---------------------|-----------------|
| 1 | Decision valide | `trader_output.metadata.decision` | Pas BUY ni SELL → `skipped` si HOLD, `blocked` sinon | `skipped` ou `blocked` |
| 2 | Risk-manager a validé | `risk_output.metadata.accepted` | `accepted=false` | `refused` |
| 3 | Paramètres complets | trader + risk metadata | `volume ≤ 0` ou `entry` absente ou `stop_loss` absente | `blocked` |
| 4 | Cohérence side | trader decision vs proposé | Side incohérent entre trader et risk | `blocked` |
| 5 | Marché ouvert | horloge UTC + asset class | Weekend forex, hors session | `blocked` |
| 6 | Spread acceptable | `snapshot.spread`, `snapshot.last_price` | `spread_pct > max_spread_pct` | `blocked` |
| 7 | Volume broker-compatible | `RiskEngine._volume_limits()` | `volume < min_vol` ou `volume > max_vol` | `blocked` |
| 8 | Instrument tradable | `InstrumentClassifier.classify()` | Asset class non supporté pour le mode | `blocked` |

**Logique du check 5 (Market Hours) :**

```python
def _is_market_open(self, pair: str) -> tuple[bool, str]:
    """Check if market is open for this instrument.

    Forex: Sun 22:00 UTC → Fri 22:00 UTC
    Crypto: 24/7
    Indices/Equities: check exchange hours (simplified)
    """
```

- Forex : fermé samedi toute la journée, vendredi après 22:00 UTC, dimanche avant 22:00 UTC
- Crypto : toujours ouvert
- Indices/Equities : simplifié — Lu-Ve 08:00-21:00 UTC
- Metals/Energy : Lu-Ve 01:00-22:00 UTC (avec pause)

**Logique du check 6 (Spread) :**

```python
# Seuils de spread max par mode (en % du prix)
MAX_SPREAD_PCT = {
    "simulation": 0.05,   # 5 bps — très permissif
    "paper": 0.02,        # 2 bps
    "live": 0.01,         # 1 bp — strict
}
```

Calcul : `spread_pct = snapshot["spread"] / snapshot["last_price"] * 100`

**Statut:** [x] Terminé

---

### Tache 3 : Config `execution_manager_llm_enabled`

**Fichier:** `backend/app/core/config.py` (modifier)

Ajouter au `Settings` :

```python
execution_manager_llm_enabled: bool = Field(default=False, alias='EXECUTION_MANAGER_LLM_ENABLED')
```

**Statut:** [x] Terminé

---

### Tache 4 : Intégrer le preflight dans le pipeline (registry.py)

**Fichier:** `backend/app/services/agentscope/registry.py` (modifier)

Modifier Phase 4, dans la boucle `for name in ["trader-agent", "risk-manager", "execution-manager"]` :

Quand `name == "execution-manager"` :

```python
# 1. Preflight (toujours)
from app.services.execution.preflight import ExecutionPreflightEngine
preflight_engine = ExecutionPreflightEngine()
preflight_result = preflight_engine.validate(
    trader_output=_trader_out,
    risk_output=_risk_out,
    snapshot=snapshot,
    pair=pair,
    mode=getattr(run, "mode", "simulation"),
)

# 2. Execution réelle (si preflight OK et pas simulation)
exec_result = None
if preflight_result.can_execute and preflight_result.status != ExecutionStatus.SIMULATED:
    from app.services.execution.executor import ExecutionService
    exec_svc = ExecutionService()
    exec_result = await exec_svc.execute(
        run_id=run.id,
        mode=preflight_result.mode,
        symbol=preflight_result.symbol,
        side=preflight_result.side,
        volume=preflight_result.volume,
        stop_loss=preflight_result.stop_loss,
        take_profit=preflight_result.take_profit,
        metaapi_account_ref=metaapi_account_ref,
    )

# 3. LLM résumé (optionnel)
if settings.execution_manager_llm_enabled:
    # Appel LLM avec le résultat preflight + exec pour résumé
    current_msg = await _call_agent("execution-manager", current_msg, ...)
else:
    # Résumé déterministe
    exec_status = exec_result.get("status", preflight_result.status.value) if exec_result else preflight_result.status.value
    hold_meta = {
        "decision": preflight_result.side or "HOLD",
        "should_execute": preflight_result.can_execute,
        "side": preflight_result.side,
        "volume": preflight_result.volume,
        "status": exec_status,
        "reason": preflight_result.reason,
        "preflight": {
            "checks_passed": preflight_result.checks_passed,
            "checks_failed": preflight_result.checks_failed,
        },
        "degraded": False,
    }
    current_msg = Msg("execution-manager", f"status={exec_status}, reason={preflight_result.reason}", "assistant", metadata=hold_meta)
```

**Impact sur le HOLD bypass :** Le HOLD bypass existant (lignes 1574-1594) reste intact — le preflight engine le gère aussi (Check 1 : decision == HOLD → `skipped`).

**Statut:** [x] Terminé

---

### Tache 5 : Mettre à jour le prompt execution-manager (LLM optionnel)

**Fichier:** `backend/app/services/agentscope/prompts.py` (modifier)

Enrichir le prompt pour que le LLM (quand activé) ait le contexte du preflight :

```python
"execution-manager": {
    "system": (
        "You are the execution manager. Your role is to provide a clear, human-readable "
        "summary of the execution decision.\n\n"
        "Rules:\n"
        "- The preflight engine has already validated all operational conditions.\n"
        "- You summarize the preflight result and execution outcome.\n"
        "- NEVER change the decision, side, volume, or any trade parameter.\n"
        "- NEVER transform HOLD into BUY/SELL.\n"
        "- If preflight blocked the trade, explain why clearly.\n"
        "- If the trade was executed, confirm the details.\n"
    ),
    "user": (
        "Instrument: {pair}\nTimeframe: {timeframe}\nMode: {mode}\n\n"
        "Preflight result: {preflight_result}\n"
        "Execution result: {execution_result}\n"
        "Trader decision: {trader_decision}\n"
        "Risk manager result: {risk_result}\n\n"
        "Provide a concise summary of what happened and why.\n"
        "Respond with:\n"
        "- decision=BUY|SELL|HOLD\n"
        "- should_execute=true|false\n"
        "- status=executed|simulated|blocked|refused|skipped|failed\n"
        "- reason=<explanation>\n"
    ),
},
```

**Statut:** [x] Terminé

---

### Tache 6 : Enrichir les debug traces avec le preflight

**Fichier:** `backend/app/services/agentscope/registry.py` (modifier)

L'output de l'execution-manager dans les traces doit inclure le détail du preflight :

```json
{
  "decision": "BUY",
  "should_execute": true,
  "status": "simulated",
  "side": "BUY",
  "volume": 0.15,
  "reason": "All preflight checks passed. Order simulated.",
  "preflight": {
    "checks_passed": [
      "decision_valid: BUY",
      "risk_accepted: true",
      "params_complete: entry=1.1550, sl=1.1500, tp=1.1650, vol=0.15",
      "side_consistent: BUY",
      "market_open: forex session active",
      "spread_ok: 0.002% < 5.000%",
      "volume_ok: 0.15 within [0.01, 10.0]",
      "instrument_tradable: forex supported"
    ],
    "checks_failed": []
  },
  "llm_enabled": false
}
```

**Exemple de blocage :**

```json
{
  "decision": "BUY",
  "should_execute": false,
  "status": "blocked",
  "side": "BUY",
  "volume": 0.15,
  "reason": "BLOCKED: market closed (Saturday)",
  "preflight": {
    "checks_passed": [
      "decision_valid: BUY",
      "risk_accepted: true",
      "params_complete: entry=1.1550, sl=1.1500, tp=1.1650, vol=0.15",
      "side_consistent: BUY"
    ],
    "checks_failed": [
      "market_open: FAILED — forex market closed (Saturday)"
    ]
  },
  "llm_enabled": false
}
```

**Statut:** [x] Terminé

---

### Tache 7 : Aligner ExecutionService.execute() avec ExecutionStatus

**Fichier:** `backend/app/services/execution/executor.py` (modifier)

Remplacer les statuts en dur (`"simulated"`, `"submitted"`, `"failed"`, etc.) par l'enum `ExecutionStatus` :

| Ancien statut | Nouveau statut |
|--------------|----------------|
| `"created"` | Supprimé (état transitoire interne) |
| `"simulated"` | `ExecutionStatus.SIMULATED` |
| `"submitted"` | `ExecutionStatus.EXECUTED` |
| `"failed"` | `ExecutionStatus.FAILED` |
| `"blocked"` | `ExecutionStatus.BLOCKED` |
| `"paper-simulated"` | `ExecutionStatus.SIMULATED` (unifié) |

**Statut:** [x] Terminé

---

### Tache 8 : Tests unitaires

**Fichier:** `backend/tests/unit/test_execution_preflight.py` (nouveau)

| Test | Scenario |
|------|----------|
| `test_hold_decision_skipped` | HOLD → status `skipped`, can_execute=false |
| `test_risk_rejected_refused` | risk accepted=false → status `refused` |
| `test_missing_volume_blocked` | volume=0 → status `blocked` |
| `test_missing_stop_loss_blocked` | SL absent → status `blocked` |
| `test_missing_entry_blocked` | entry=0 → status `blocked` |
| `test_side_flip_blocked` | trader=BUY, risk output implies SELL → status `blocked` |
| `test_market_closed_saturday_blocked` | Samedi UTC → status `blocked` |
| `test_market_closed_friday_late_blocked` | Vendredi 23:00 UTC → status `blocked` |
| `test_market_open_weekday` | Mardi 14:00 UTC → passe le check |
| `test_crypto_always_open` | Crypto samedi → passe le check |
| `test_spread_excessive_blocked` | spread 0.05% avec limit 0.01% (live) → status `blocked` |
| `test_spread_acceptable` | spread 0.005% avec limit 0.01% → passe |
| `test_volume_below_min_blocked` | volume 0.001 avec min 0.01 → status `blocked` |
| `test_volume_above_max_blocked` | volume 100 avec max 10 → status `blocked` |
| `test_all_checks_pass_simulation` | Tout OK en simulation → status `simulated`, can_execute=true |
| `test_all_checks_pass_live` | Tout OK en live → status `executed`, can_execute=true |
| `test_checks_passed_list_complete` | Vérifier que checks_passed contient tous les checks |
| `test_deterministic_summary_no_llm` | LLM disabled → résumé généré par le code |

**Statut:** [x] Terminé

---

## Fichiers impactés (résumé)

| Fichier | Action |
|---------|--------|
| `backend/app/services/execution/preflight.py` | Nouveau |
| `backend/app/services/execution/executor.py` | Modifier (aligner sur ExecutionStatus) |
| `backend/app/services/agentscope/registry.py` | Modifier (preflight dans Phase 4) |
| `backend/app/services/agentscope/prompts.py` | Modifier (prompt LLM optionnel) |
| `backend/app/core/config.py` | Modifier (ajouter config) |
| `backend/tests/unit/test_execution_preflight.py` | Nouveau |

---

## Critères de succès

1. [x] Un ordre sans side/volume/SL est bloqué avec raison claire — tests `test_missing_volume_blocked`, `test_missing_stop_loss_blocked`, `test_missing_entry_blocked`
2. [x] Un ordre refusé par le risk-manager retourne `refused` — test `test_risk_rejected_refused`
3. [x] Le marché fermé (weekend forex) bloque l'exécution — tests `test_market_closed_saturday_blocked`, `test_market_closed_friday_late_blocked`
4. [x] Un spread excessif bloque l'exécution — test `test_spread_excessive_blocked`
5. [x] Le volume est validé contre les contraintes broker — tests `test_volume_below_min_blocked`, `test_volume_above_max_blocked`
6. [x] Le LLM est désactivé par défaut — `config.py:execution_manager_llm_enabled=False`
7. [x] Les 6 statuts sont correctement attribués — tests `test_all_checks_pass_simulation`, `test_all_checks_pass_live`, `test_hold_decision_skipped`, etc.
8. [x] Aucun flip BUY→SELL ou HOLD→BUY n'est possible — test `test_side_flip_blocked`, check 4
9. [x] Les debug traces contiennent le détail du preflight — `registry.py` preflight metadata avec checks_passed/failed
10. [x] Pas de régression — 455 passed, 0 régression

**Tests:** 19 nouveaux tests, 455 total passed
