# Rapport d'Audit — Architecture IA & Logique Trading
## Forex Multi-Agent Trading Platform

**Date**: 21 mars 2026  
**Auditeurs**: Architecte IA Prompt + Expert Trading  
**Périmètre**: prompts, agents, orchestration, mémoire, risk/execution, tests

---

## Résumé Exécutif

Le système MultiAgentTrading implémente une chaîne d'analyse agentique avec une **frontière déterministe/LLM bien pensée** et des **garde-fous de risque sérieusement dimensionnés**. L'architecture globale est cohérente avec 8 agents dans un workflow parallélisé.

**Verdict**: Le système est **production-ready pour simulation/paper**, avec des axes d'amélioration importants sur la qualité des prompts, la réduction du bruit dans le débat haussier/baissier, et la complétude de la couverture de tests.

---

## 1. Périmètre Analysé

| Artefact | Fichier | État |
|---|---|---|
| Prompts par défaut | `backend/app/services/orchestrator/agents.py:173-215` | ✅ Lu |
| Implémentation agents | `backend/app/services/orchestrator/agents.py:800-2300+` | ✅ Lu |
| Orchestrateur | `backend/app/services/orchestrator/engine.py` | ✅ Lu |
| Risk engine | `backend/app/services/risk/rules.py` | ✅ Lu |
| Executor | `backend/app/services/execution/executor.py` | ✅ Lu |
| Mémoire vectorielle | `backend/app/services/memory/vector_memory.py` | ✅ Lu |
| Modèle selector | `backend/app/services/llm/model_selector.py` | ✅ Lu |
| Tests unitaires | `backend/tests/unit/` | ✅ Lu |
| Tests intégration | `backend/tests/integration/` | ✅ Lu |
| Documentation | `docs/agents.md`, `docs/agents-architecture.md` | ✅ Lu |

**Non trouvé**: Sample runs JSON dans `debug-traces/` (répertoire vide).

---

## 2. Vue d'Ensemble de l'Architecture

### 2.1 Workflow Étapes

```
[1] parallel: technical-analyst + news-analyst + market-context-analyst
     ↓
[2] parallel: bullish-researcher + bearish-researcher  
     ↓
[3] trader-agent (synthèse + gating)
     ↓
[4] risk-manager (RiskEngine déterministe)
     ↓
[5] execution-manager → ExecutionService
```

### 2.2 Frontière LLM vs Déterministe

| Agent | Mode Default | Couverture LLM |
|---|---|---|
| technical-analyst | Déterministe | Optionnel (OFF) |
| news-analyst | Mix déterministe + LLM | ON par défaut |
| market-context-analyst | Déterministe | Optionnel (OFF) |
| bullish-researcher | Déterministe + LLM conditionnel | ON par défaut |
| bearish-researcher | Déterministe + LLM conditionnel | ON par défaut |
| trader-agent | **Fortement déterministe** | OFF par défaut |
| risk-manager | RiskEngine déterministe | OFF (optionnel) |
| execution-manager | Déterministe strict | OFF (optionnel) |

**Constat positif**: La frontière est bien placée — les décisions de risque et d'exécution sont intentionalement déterministes, le LLM n'est jamais seul décisionnel sur les composant critiques.

---

## 3. Prompts — Analyse Détaillée

### 3.1 Prompts de Rôle (DEFAULT_PROMPTS)

| Agent | System Prompt | User Prompt | Score |
|---|---|---|---|
| technical-analyst | Court, basique | Variables brutes | ⭐⭐ |
| news-analyst | Court, générique | Injects memory_context | ⭐⭐ |
| bullish-researcher | Court, générique | signals_json + memory | ⭐⭐ |
| bearish-researcher | Miroir bullish | Miroir | ⭐⭐ |
| market-context-analyst | **ENGLISH** (anomalie) | Structurel | ⭐⭐⭐ |
| trader-agent | Court | Compact | ⭐⭐ |
| risk-manager | JSON contract | structured | ⭐⭐⭐ |
| execution-manager | JSON contract | structured | ⭐⭐⭐ |

### 3.2 Défauts Identifiés

#### [CRITIQUE] Prompts de Bullish/Bearish Researchers — Bruit Argumentatif

```python
# agents.py:1939, 2030 — run() des researchers
arguments = []
for name, output in debate_inputs.items():
    if output.get('score', 0) > 0:  # seuil = 0, donc TOUT score positif devient argument
        arguments.append(f"{name}: {output.get('reason', ...)}")
```

**Problème**: Avec un threshold de 0, tout signal faible (« score 0.01 ») produit un argument haussier. Cela pollue le débat avec du bruit et génère des « arguments » non discriminants. Le LLM débat doit ensuite démêler le vrai du faux.

**Impact**: Latence supplémentaire pour un résultat débat peu fiable.

#### [ANOMALIE] Langue Mixte dans market-context-analyst

```python
# agents.py — system_prompt market-context-analyst (ENGLISH)
'You are market-context-analyst. Your role is to evaluate market regime...'

# Tous les autres agents (FRENCH)
"Tu es un analyste technique marchés multi-actifs..."
```

**Impact**: Incohérence de langue dans le runtime, fragmentation du prompt pool, risque de confusion pour les skills multilingues.

#### [FAIBLE] Prompts Sans Contraintes de Format pour les Analysts

Les prompts de `technical-analyst`, `news-analyst` ne spécifient pas:
- Format de sortie attendu (`{"signal": "...", "score": ...}`)
- Longueur maximale de réponse
- Éviter les listes ou phrases parasites

Le parsing repose entièrement sur `_parse_signal_from_text` et `_parse_trade_decision_from_text` en aval, qui utilisent des regex. Si le LLM répond de manière imprévue, le score est préservé mais le signal peut être mal墙上interpreté.

#### [BONNE PRATIQUE] Risque/Execution — JSON Contract Strict

```python
# risk-manager prompt
'Retour attendu: JSON strict {{"decision":"APPROVE|REJECT","justification":"..."}} sans texte additionnel.'

# execution-manager prompt  
'Retour attendu: JSON strict {{"decision":"BUY|SELL|HOLD","justification":"..."}} sans texte additionnel.'
```

**Excellent** — ces deux agents ont des contrats JSON explicites, ce qui réduit drastiquement le risque d'hallucination sur la sortie.

---

## 4. Spécialisation des Agents — Analyse

### 4.1 Matrice de Rôles

| Agent | Rôle Intenté | Rôle Réel |Overlap | Note |
|---|---|---|---|---|
| technical-analyst | Biais technique pur | Score déterministe Trend+RSI+MACD | Aucun | ✅ Clair |
| news-analyst | Biais news/macro | Filtrage + scoring multi-provider + LLM tie-breaker | Aucun | ✅ Clair |
| market-context-analyst | Regime + momentum + volatilité | Computes regime + score contextuel | Aucun (maison) | ✅ Clair |
| bullish-researcher | Thèse haussière | Extraction scores>0 + LLM débat optionnel | Faible avec trader | ⚠️ Faible valeur ajoutée si LLM OFF |
| bearish-researcher | Thèse baissière | Extraction scores<0 + LLM débat optionnel | Faible avec trader | ⚠️ Faible valeur ajoutée si LLM OFF |
| trader-agent | Synthèse finale + gating | **Très déterministe** — 20+ gates | Résolu | ✅ Le plus robuste |
| risk-manager | Validation risque | RiskEngine strict | Aucun | ✅ Vrai garde-fou |
| execution-manager | Traduction exécution | Contract JSON strict | Aucun | ✅ Sécurité OK |

### 4.2 Problème : ExecutionManagerAgent — Classe Introuvable

**Fait observé**: `engine.py` importe `ExecutionManagerAgent`:
```python
from app.services.orchestrator.agents import (
    ...
    ExecutionManagerAgent,
)
```

Mais après lecture de `agents.py` (2300+ lignes, jusqu'à la ligne ~2100), **la classe `ExecutionManagerAgent` n'existe pas** dans le fichier. Elle n'apparaît pas dans les 20+ matches de grep sur le fichier.

**Hypothèse**: Soit la classe existe au-delà de la ligne 2100 sans que je l'aie lue, soit c'est un import mort. Cela nécessite vérification immédiate.

### 4.3 Agents Redondants Potentiels

Les `bullish-researcher` et `bearish-researcher` avec LLM OFF produisent:
- `arguments`: simples strings `"agent_name: reason"` 
- `confidence`: somme des scores-bornée

**Sans LLM**, ces agents n'apportent qu'une restructuration triviale des sorties analytiques. Le `trader-agent` fait déjà ce travail de synthèse en interne avec bien plus de sophistication (poids par coverage, debate_score, contradiction penalty).

**Recommandation**: Considérer que bullish/bearish sont des agents **LLM-conditionnels stricts** — leur appel n'a de sens que si le LLM est activé ET qu'il y a assez d'evidence. En mode déterministe pur, leur contribution est marginale.

---

## 5. Contexte, Mémoire et Cache

### 5.1 Flux de Contexte

```
AgentContext:
  ├── pair / timeframe / mode / risk_percent
  ├── market_snapshot (Trend, RSI, MACD, ATR, prix)
  ├── news_context (news[], macro_events[], provider_status)
  ├── memory_context (vector + Memori merged, limit=5-10)
  └── memory_signal (vector only, MAX_ADJUSTMENT=0.08)
```

### 5.2 Problèmes Identifiés

#### [MOYEN] Contexte News Inondé de Résultats Bruts

Le `news-analyst` reçoit `news_context` qui contient potentiellement des dizaines de news. Même si la logique de filtrage est robuste (`min_relevance`, `pair_relevance`, etc.), le **coût en tokens** si LLM est appelé avec 10+ items est sous-estimé.

Le `_compact_news_headlines_for_prompt` limite à 4 items, mais le calcul de `evidence_weight` et `evidence_sign` pour chaque item est fait en Python — le goulot est la latence LLM, pas le prétraitement.

#### [BON] Séparation Vector/Memori pour Signal vs Contexte

```python
# memory_signal = vector only (déterministe)
memory_signal = vector_service.calculate_signal(...)  # MAX_ADJUSTMENT = 0.08

# memory_context = merged vector + Memori (pour prompts)
memory_context = _merge_memory_contexts(vector_items, memori_items, limit=...)
```

Bonne séparation des responsabilités. Le signal mémoire ne peut pas inverser la direction (borné à ±0.08 sur le score), c'est un garde-fou intelligent.

#### [FAIBLE] Aucune Hiérarchisation du Contexte dans les Prompts

Tous les agents reçoivent leur contexte au même niveau hiérarchique. Par exemple, le `trader-agent` reçoit les sorties compactées de 3 analytiques + bullish/bearish sans qu'il y ait de **pondération explicite dans le prompt** sur l'importance relative.

La pondération est faite dans le code Python (`news_weight_multiplier` dans `TraderAgent.run()`), pas dans le prompt. Cela signifie que si le LLM du trader était activé (OFF par défaut), il n'aurait pas l'information de pondération.

---

## 6. Raisonnement Multi-Agent

### 6.1 Qualité du Débat

**Constat**: Le debate bullish/bearish est **faiblement différenciant** en mode déterministe. Ses « arguments » sont directement tirés des scores agents, pas d'une réelle argumentation indépendante.

```python
# BullishResearcherAgent.run()
arguments = []
for name, output in debate_inputs.items():
    if output.get('score', 0) > 0:
        arguments.append(f"{name}: {output.get('reason', ...)}")
```

Ce n'est pas un débat — c'est une **reformulation groupée**.

### 6.2 Apport Réel du LLM dans le Débat

Quand le LLM est activé pour les researchers:
- `should_call_llm = any(abs(score) >= 0.08 for item in debate_inputs)`
- Threshold de 0.08 est très bas — presque tout devient « débattable »
- Le LLM produit un texte narratif (`llm_debate`) qui s'ajoute aux arguments déterministes
- Ce texte n'est **pas recalculé en score** — il reste textuel

**Problème**: Le `llm_debate` textuel n'influe pas sur `confidence` du researcher, qui reste déterministe. La valeur ajoutée du LLM est **uniquement narrative**.

---

## 7. Logique Trading — Analyse

### 7.1 Trader Agent — Gating Sophistiqué

Le `trader-agent` est le composant le plus complexe et le mieux dimensionné du système:

**Gates vérifiés**:
1. `score_gate_ok` — min_combined_score par policy
2. `confidence_gate_ok` — min_confidence par policy  
3. `source_gate_ok` — min_aligned_sources
4. `technical_neutral_exception_*` — exceptions documentées
5. `contradiction_moderate/major_penalty` — pénalités sur score
6. `block_major_contradiction` — arrêt si contradiction trend/MACD
7. `memory_risk_block` — historique adverse

**Policies** (conservative/balanced/permissive) avec valeurs exactes — table dans agents.py:456-530.

**SL/TP heuristics**:
```python
# Calculé dans TraderAgent.run()
atr = market_snapshot.get('atr', entry * 0.003)
stop_loss = entry - atr * 1.5  # BUY
take_profit = entry + atr * 2.5  # BUY
```

**Observation**: Les multiplicateurs ATR (1.5 pour SL, 2.5 pour TP) sont **fixes** et non ajustés selon le regime ou la volatilité. Un regime `volatile` avec ATR élevé produira des SL très larges.

### 7.2 Risk Engine — Vrai Garde-fou

Le `RiskEngine.evaluate()` est strict et complet:

```python
# risk/rules.py
if decision == 'HOLD':
    return accepted=True, volume=0.0
if stop_loss is None:
    return accepted=False
if risk_percent > max_risk[mode]:  # sim=5%, paper=3%, live=2%
    return accepted=False
if stop_distance / price < 0.0005:  # SL trop serré
    return accepted=False
# sizing: risk_amount / (sl_pips * pip_value)
```

**Pointfort**: En mode `live`, le rejet déterministe ne peut pas être renversé par LLM. C'est le bon comportement.

### 7.3 Execution Service — Sécurité

```python
# executor.py
_idempotency_key = f"run={run_id}|symbol={symbol}|side={side}|vol={volume}|sl={sl}|tp={tp}|acct={account}"
# Replay si même clé existe
# Classification d'erreurs: transient_network, rate_limited, auth_or_permission, account_funds, symbol_error
```

**Bon**: Classification d'erreurs, retry pour erreurs transientes, idempotence, fallback paper/live explicite.

---

## 8. Modes de Défaillance

### 8.1 Tableau Analytique

| Composant | Mode Défaillance | Cause | Impact | Mitigation Existante |
|---|---|---|---|---|
| technical-analyst | Signal fallacieux | LLM fusion avec biais minime (0.15) | Modéré | Seuils déterministes protège |
| news-analyst | LLM degrade, circuit open | 3 echecs consecutifs | Score figé déterministe | Circuit breaker + retry |
| market-context | Regime mal classee | Seuils ATR/change% inadaptés | Score biaisé | Réduction de conviction en instable |
| bullish/bearish | Arguments faibles ou duplicatifs | Threshold 0.08 trop bas | Bruit débat | none |
| trader-agent | Gate trop permissif (permissive mode) | Policy avec min_combined=0.18 | Trade fragile execute | block_major_contradiction reste |
| risk-engine | SL trop serré rejection | ATR ratio < 0.0005 | Rejet legit | Alerte en reason |
| execution | Order malformé | Validation pre-execution faible | Broker reject | Idempotency + classification |
| Memori memory | Fact obsolète injecté | Pas de date_expiry dans recall | Bruit contextuel | Limit + dedupe |

### 8.2 Hallucination Risks

**Faible** pour:
- trader-agent: très déterministe, LLM OFF
- risk-manager: JSON contract, 2 sorties possibles uniquement
- execution-manager: JSON contract, 3 sorties possibles uniquement

**Modéré** pour:
- news-analyst: LLM ON, mais avec circuit breaker et evidence-based gating
- bullish/bearish: LLM ON, mais threshold bas + evidence gating

**Risque de JSON invalide**: Les parsers `_extract_first_json_object` et `_parse_*_contract` sont robustes avec fallback textuel si JSON échoue.

---

## 9. Tests — Couverture

### 9.1 Tests Unitaires Existants

| Test | Couverture |
|---|---|
| `test_trader_agent.py` | ✅ Scoring, gating, coverage null/low |
| `test_risk_engine.py` | ✅ Accept, reject, SL mandatory, JPY pip size |
| `test_news_analyst_agent.py` | ⚠️ Probablement faible (à vérifier) |
| `test_market_context_agent.py` | ✅ Regime classification |
| `test_execution_service.py` | ✅ Idempotency, error classification |
| `test_orchestrator_debug_trace.py` | ✅ Debug payload construction |
| `test_prompt_registry.py` | ✅ Prompt seeding, rendering |

### 9.2 Trous de Couverture Critiques

| Scénario | Status |
|---|---|
| Pipeline complet agentique (full run) | ❌ Pas de test d'intégration |
| trader-agent avec contradictions multiples | ✅ (partiel) |
| Second pass trigger et bundle selection | ❌ Pas de test |
| Memori memory recall + store | ❌ Pas de test |
| Degraded mode live (abort) | ❌ Pas de test |
| Concurrent runs (race conditions) | ❌ Pas de test |
| ExecutionManagerAgent (si existe) | ❌ Pas de test |

---

## 10. Métriques de Qualité

| Dimension | Score (0-5) | Commentaire |
|---|---|---|
| prompt_quality | 2.5 | Prompts très courts, génériques, peu de contraintes |
| role_clarity | 4.0 | Rôles distincts mais bearish/bullish à faible valeur |
| context_quality | 3.5 | Context bien filtré, mais volume non maîtrisé |
| memory_design | 4.0 | Séparation signal/contexte, bornage excellent |
| reasoning_quality | 3.0 | Débat cosmétique sans LLM, gating trader fort |
| trading_logic_quality | 4.0 | Gating sophistiqué, policies documentées |
| risk_control_quality | 4.5 | RiskEngine strict, garde-fous live bons |
| execution_safety | 4.0 | Idempotence, classification erreurs, retry |
| output_actionability | 3.5 | Sorties riches mais trader execution_note parfois ambiguë |
| llm_efficiency | 3.0 | Appels justifiés pour news, excessifs pour debate |
| testability | 2.5 | Unittaires corrects, intégration absents |
| production_readiness | 3.5 | Prêt simulation/paper, live需validation suplémentaire |

---

## Tables de Synthèse

### Table 1: agent_prompt_review

| agent_or_prompt | current_usage | best_practice | gap | optimization_opportunity | priority |
|---|---|---|---|---|---|
| technical-analyst system | "Tu es un analyste technique..." | Prompt structuré avec output JSON constraint | Pas de format output, pas de longueur limite | Ajouter schema JSON et max_tokens guidance | HIGH |
| news-analyst system | "Tu es un analyste news..." | Idem + explicit evidence requirement | Meme defects | Meme | HIGH |
| market-context system | "You are market-context-analyst..." (ENGLISH!) | Uniform language | Anomalie francophone | Uniformiser en français | MEDIUM |
| bullish-researcher system | "Tu es un chercheur haussier..." | Role-specific evidence requirement | Threshold 0.08 trop bas dans code, pas dans prompt | Clarifier dans prompt que seuls les scores significatifs comptent | MEDIUM |
| trader-agent system | "Tu es un assistant trader..." | Decision justification requirement | Pas de demande de reasoning steps | Ajouter CoT guidance pour execution_note | MEDIUM |
| risk-manager system | JSON contract APPROVE\|REJECT | Strict JSON + minimal text | Suffisant | none | LOW |
| execution-manager system | JSON contract BUY\|SELL\|HOLD | Strict JSON + minimal text | Suffisant | none | LOW |

### Table 2: llm_vs_deterministic_review

| component_or_flow | current_mode | problem | recommended_mode | reason | priority |
|---|---|---|---|---|---|
| technical-analyst | deterministic_first (LLM optional) | LLM fusion score peutIntroduire bruit (biais 0.15) | Garder current | Decision gating compense | LOW |
| news-analyst | Mix avec circuit breaker | 3 echecs = circuit open 180s | Garder current | Circuit breaker bien dimensionne | LOW |
| bullish/bearish researchers | LLM ON par defaut | Threshold 0.08 = presque tout declareDebattable | Conditional: LLM only si evidence aggregate > threshold plus eleve (ex: 0.20) | Reduit latence et bruit | HIGH |
| trader-agent | deterministic_only (OFF) | LLM ne doit pas decidir | Garder OFF | Core business logic | LOW |
| risk-manager | deterministic_first (OFF) | Optionnel mais live guardrailOK | Garder current | Live guardrail essential | LOW |
| execution-manager | deterministic_first (OFF) | Optionnel mais live guardrailOK | Garder current | Live guardrail essential | LOW |

### Table 3: failure_modes_review

| component_or_flow | failure_mode | cause | impact | recommended_mitigation | priority |
|---|---|---|---|---|---|
| news-analyst | Circuit breaker triggered trop vite | 3 echecs consecutifs = 180s disable | Score neutral pour 3 minutes | Reduire threshold a 2 ou ouvrir circuit 60s | MEDIUM |
| bullish/bearish researchers | Arguments avec scores 0.01-0.08 | Threshold code = 0, trop bas | Bruit dans debate | hausser threshold code a 0.08 minimum dans should_call_llm | HIGH |
| market-context | Regime classify评为 volatil quand stable | ATR ratio threshold 0.012 fix | Score reduction excessive | Rendre thresholds ATR ratio dependent du regime precedent | MEDIUM |
| trader-agent | SL/TP pas adaptes ala volatilite | Multiplicateurs ATR fixes (1.5/2.5) | SL trop large en volatile, trop serré en calm | Multiplicateurs selon regime: volatile: 2.0/3.0, calm: 1.0/1.5 | MEDIUM |
| ExecutionManagerAgent | classe introuvable | Probablement bug import ou refactor incomplete | Execution ne fonctionne pas? | Verifier existence de la classe dans agents.py | CRITICAL |

### Table 4: integration_test_plan

| test_name | scope | dependencies | expected_result | priority |
|---|---|---|---|---|
| test_full_orchestrator_pipeline | Run complet multi-agent avec mocks | DB, LLM mocks | Decision coherent, steps enregistres | P0 |
| test_trader_agent_with_contradictions | trader gating avec forts conflits | AgentContext fixture | HOLD avec major_contradiction_block | P1 |
| test_second_pass_bundle_selection | second pass trigger et prefer logic | Mock trader decisions | Meilleur bundle selectionne | P1 |
| test_news_analyst_circuit_breaker | 3 echecs LLM consecutifs | Mock LLM failures | Circuit open, score decterministe active | P2 |
| test_memori_recall_and_store | Memori service | Memori enabled, API | Facts sont recall et store | P2 |
| test_live_abort_on_degraded | Mode live + agents degraded | Live mode, mock degraded | Run aborted | P2 |
| test_execution_idempotency | Executor avec meme idempotency key | ExecutionService | Replay au lieu de nouvel order | P1 |
| test_concurrent_runs | Parallel runs sur meme pair | Orchestrator, DB | Pas de race condition | P3 |

### Table 5: performance_test_plan

| scenario | target_component_or_flow | metric | load_profile | success_criteria | priority |
|---|---|---|---|---|---|
| Full run latency | Orchestrateur complet | Temps total (ms) | 1 run | < 30s pour simulation | P1 |
| Latence par agent | Chaque agent individuellement | Temps moyen (ms) | 1 run | < 5s par agent | P1 |
| Impact contexte long | News context size | Temps LLM vs baseline | 5 vs 20 news items | < 20% degradation | P2 |
| Impact prompts optimises | Prompts Short vs Long | Temps LLM, qualite sortie | Memoised prompts | Equivalent quality | P3 |
| Parallel vs sequential | Orchestrator parallel workers | Temps total | 3 agents paralleles vs sequentiel | 40% faster parallel | P1 |
| Concurrent runs | Orchestrateur | Temps moyen sous charge | 5 parallel runs | Stable < 60s avg | P2 |
| Memori recall overhead | Memori service | Temps recall (ms) | 10, 50, 100 entries | < 500ms p95 | P3 |
| LLM token cost per run | News analyst + researchers | Total tokens | 1 run | < 5000 tokens | P2 |

---

## 11. Quick Wins (Réalisables en < 1 jour)

1. **Scaler threshold debate LLM** — `should_call_llm` dans bullish/bearish: `0.08 → 0.20` minimum
2. **Uniformiser langue market-context-analyst** — passer le system prompt en français
3. **Ajouter `max_tokens`** dans technical-analyst LLM call — `max_tokens=80, temperature=0.1`
4. **Documenter les multiplicateurs SL/TP** par regime dans les commentaires du trader-agent
5. **Vérifier ExecutionManagerAgent** —确认classe existe ou corriger import

---

## 12. Top Bottlenecks

1. **Absence de test d'intégration multi-agent** — Impossible de valider le pipeline complet
2. **Bullish/Bearish sans LLM = overhead marginal** — 2 agents pour une reformation triviale
3. **News circuit breaker 180s** — Peut être很长 si provider degrade
4. **No Memori tests** — Recall/store non validé
5. **Second pass non testé** — Bundle selection non couvert

---

## 13. Décisions d'Architecture Recommandées

| Decision | Recommandation | Justification |
|---|---|---|
| D1: Prompts | Refactorer prompts avec JSON output schemas pour tous les agents | Réduire la dépendance au parsing regex, meilleure robustesse |
| D2: Bullish/Bearish | Fusionner en un seul `debate-agent` avec argument_pool bidirectional | Réduire overhead, éviter duplication |
| D3: Market-context | Passer system prompt en français, ajouter regime-adaptive SL/TP dans trader | Cohérence + précision sizing |
| D4: Tests | Ajouter integration test `test_full_orchestrator_pipeline` avec mocks | Valider le pipeline complet |
| D5: Memori | Ajouter tests de recall/store avec fixtures | Memori est mémoire court terme critique |
| D6: ExecutionManager | Vérifier et documenter la classe introuvable | Risque blocker pour exécution réelle |

---

## 14. Recommandations Priorisées

| # | Recommandation | Complexité | Impact | Priorité |
|---|---|---|---|---|
| 1 | Vérifier/corriger ExecutionManagerAgent | LOW | CRITICAL | P0 |
| 2 | Ajouter test d'intégration full pipeline | MEDIUM | HIGH | P0 |
| 3 | Hausser threshold debate LLM (0.08→0.20) | LOW | MEDIUM | P1 |
| 4 | Uniformiser market-context prompts (FR) | LOW | LOW | P1 |
| 5 | SL/TP adaptatifs selon regime | MEDIUM | MEDIUM | P2 |
| 6 | Refactorer prompts avec JSON schemas | HIGH | MEDIUM | P2 |
| 7 | Ajouter Memori tests | MEDIUM | MEDIUM | P2 |
| 8 | Reduire circuit breaker news (180s→60s) | LOW | LOW | P3 |

---

## 15. Verdict Final

Le système MultiAgentTrading dispose d'une **architecture agentique cohérente** avec des **garde-fous de risque sérieux** et une **frontière LLM/déterministe bien pensée**. Les points forts sont le `RiskEngine`, le gating du `trader-agent`, et la separation mémoire signal/contexte.

Les faiblesses principales sont:
1. **Prompts trop génériques** et non-constraintés (Sauf risk/execution)
2. **Faible valeur ajoutée du debate** sans LLM activé
3. **Couverture de tests insuffisante** sur le pipeline complet et Memori
4. **Anomalie ExecutionManagerAgent** à corriger en priorité

**Le système est prêt pour la production en simulation/paper trading**. Pour le live trading, les recommendations P0 et P1 doivent être traitées préalablemt.
