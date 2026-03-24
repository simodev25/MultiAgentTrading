# Rapport d'audit approfondi — Multi-Agent Trading Platform
# Architecture IA, Prompt Engineering, Runtime Agentique & Logique Trading

**Date** : 2026-03-23
**Périmètre** : `backend/`, `infra/`, `frontend/` (lecture seule)
**Branche** : `feature/claude`
**Tests** : 357 passed, 0 failed
**Méthode** : Lecture exhaustive du code source, pas d'inférence depuis la documentation

---

## 1. Résumé exécutif

La plateforme est un **système multi-agent de trading multi-produit** piloté par LLM, composé de 8 agents spécialisés orchestrés en pipeline avec parallélisme partiel. L'architecture repose sur un serveur MCP exposant 19 outils déterministes, un moteur de risque indépendant du LLM, une mémoire vectorielle Qdrant 64 dimensions avec pondération par outcome, et une observabilité Prometheus/Grafana.

**Forces principales observées** :
- Frontière LLM/déterministe exemplaire : 4 agents LLM OFF par défaut, RiskEngine 100% déterministe
- Logique de décision trading sophistiquée : 3 politiques (conservative/balanced/permissive), détection de contradictions, gating multi-sources
- Prompts structurés avec contrats de sortie explicites et gardes anti-hallucination
- Mémoire outcome-weighted avec risk blocks automatiques

**Faiblesses principales observées** :
- Prompts recherche bull/bear quasi-identiques → risque de débat symétrique stérile
- Embedding 64-dim SHA256 sans sémantique réelle → recall mémoire limité
- Fichiers monolithiques (agents.py: 4 773L, engine.py: 1 657L)
- Position sizing dupliqué entre MCP tool et RiskEngine

### Scores par dimension (0-5)

| Dimension | Score | Justification (preuve observée) |
|-----------|-------|--------------------------------|
| Qualité des prompts | 3.5 | Contrats de sortie explicites, gardes anti-hallucination, mais prompts bull/bear identiques et certains trop longs |
| Clarté des rôles | 4.0 | 8 agents distincts, séparation claire analyse/débat/décision/risque/exécution |
| Qualité du runtime | 4.0 | Second-pass, stagnation guard, bundle selection, mais complexité du fichier engine.py |
| Gouvernance outils | 4.5 | enabled_tools enforced, alias resolution, double-check alias+canonical |
| Qualité du contexte | 3.5 | Compaction pour débat, mémoire injectée, mais contexte parfois trop large |
| Design mémoire | 3.0 | Outcome weighting, risk blocks, mais embedding SHA256 sans sémantique réelle |
| Qualité du raisonnement | 3.5 | Contradiction detection, multi-source alignment, mais dépendance au LLM pour synthèse |
| Logique trading | 4.0 | 3 modes décision, gating multi-niveaux, SL/TP ATR-based, memory risk blocks |
| Contrôle du risque | 4.5 | RiskEngine 100% déterministe, 8 classes d'actifs, barrière live, volume clamping |
| Sécurité d'exécution | 4.0 | Side flip blocked, degraded→HOLD, JSON contract strict, live abort on degradation |
| Actionnabilité sortie | 3.5 | Décisions BUY/SELL/HOLD claires, SL/TP calculés, mais rationale parfois verbose |
| Efficience LLM | 3.5 | 4 agents LLM OFF, token limits (96/384), mais débat bull/bear coûteux vs valeur |
| Observabilité | 3.0 | Prometheus metrics, trace context, mais OpenTelemetry OFF, pas d'alerting |
| Testabilité | 3.5 | 357 tests, bonne couverture agents, mais gaps E2E et cascading degradation |
| Production readiness | 3.0 | Simulation/paper solides, live gate robuste, mais credentials en clair, naming forex résiduel |

**Score moyen : 3.63/5**

---

## 2. Périmètre réellement analysé

| Couche | Fichiers lus intégralement | Lignes |
|--------|---------------------------|--------|
| Agents (prompts, logique, contrats) | `agents.py` | 4 773 |
| Orchestrateur (pipeline, autonomy) | `engine.py` | 1 657 |
| MCP server (19 outils) | `mcp_trading_server.py` | ~1 200 |
| MCP client (adapter, alias) | `mcp_client.py` | 358 |
| LangChain tools (wrappers) | `langchain_tools.py` | ~300 |
| Risk engine | `rules.py` | ~800 |
| Order guardian | `order_guardian.py` | ~600 |
| Mémoire vectorielle | `vector_memory.py` | 1 182 |
| Mémoire Memori | `memori_memory.py` | 328 |
| Model selector | `model_selector.py` | 538 |
| LLM helpers + clients | `base_llm_helpers.py`, `openai_compatible_client.py`, `ollama_client.py` | ~800 |
| Prompt registry | `registry.py` | 456 |
| Config | `config.py` | 307 |
| DB models (15 fichiers) | `db/models/*.py` | ~1 500 |
| Routes API (12 fichiers) | `api/routes/*.py` | ~2 500 |
| Tests (33 fichiers) | `tests/unit/`, `tests/integration/` | ~4 500 |
| Infra | `Chart.yaml`, `docker-compose.yml`, `Dockerfile` | ~300 |
| **Total** | **~116 fichiers Python** | **~29 900** |

---

## 3. Architecture réellement observée

### 3.1 Fait vérifié : la plateforme est multi-produit

**Preuve** : `_CONTRACT_SPECS` dans `rules.py` définit 8 classes d'actifs (forex, crypto, index, metal, energy, commodity, equity, etf). `InstrumentClassifier` dans `instrument_helpers.py` classifie automatiquement les symboles. `Settings` configure forex + crypto pairs par défaut.

**Divergence naming** : `forex.db` (SQLite default), `forex_long_term_memory` (Qdrant collection), Docker credentials `forex:forex` — naming résiduel incohérent avec l'architecture multi-produit réelle.

### 3.2 Pipeline d'agents vérifié

```
┌──────────────── Parallel Group 1 ─────────────────┐
│ TechnicalAnalyst · NewsAnalyst · MarketContext      │
│ (LLM OFF)         (LLM ON)      (LLM OFF)         │
└─────────────────────┬─────────────────────────────┘
                      ▼ _compact_analysis_outputs_for_debate()
┌──────────────── Parallel Group 2 ─────────────────┐
│    BullishResearcher (LLM ON) · BearishResearcher (LLM ON)  │
└─────────────────────┬─────────────────────────────┘
                      ▼ Full analysis_outputs + debate results
              ┌─── Sequential ───┐
              │  TraderAgent      │ (LLM OFF default)
              │  RiskManager      │ (LLM OFF default, RiskEngine 100% déterministe)
              │  ExecutionManager │ (LLM OFF default)
              └──────────────────┘
```

**Fait observé** (`model_selector.py:74-85`) : `DEFAULT_AGENT_LLM_ENABLED` montre trader-agent LLM **OFF** par défaut — la décision trading est donc déterministe par défaut, pas LLM-driven. C'est un choix fort et correct.

### 3.3 Autonomy loop vérifié

**Preuve** (`engine.py:1306-1437`) :
- `max_cycles` configurable (default 3), stagnation guard, bundle selection
- Memory refresh progressif (limit_step increment)
- Model override boost pour agents dégradés
- Second-pass avec contrôle d'attempt limits

---

## 4. Analyse des prompts

### 4.1 Structure hiérarchique observée

**3 niveaux de prompts** :
1. **Prompt registry** (`registry.py`) : 11 templates DB-backed avec versioning et activation
2. **Fallback prompts** : Hardcodés dans chaque agent class (agents.py)
3. **Language directives** : Injections françaises (`LANGUAGE_DIRECTIVE_BASE`, `_TRADING_LABELS`, `_RISK`, `_EXECUTION`, `_JSON`)

**Fait observé** : Les prompts sont rendus via `PromptTemplateService.render()` qui :
- Charge depuis DB si disponible
- Fallback vers les constantes hardcodées
- Substitue les variables avec `SafeDict` (variables manquantes marquées `{missing_key}`)
- Injecte les skills (`_append_skills_block`) et la directive langue

### 4.2 Table : Revue des prompts par agent

| Agent/Prompt | Usage actuel | Best practice | Gap | Opportunité | Priorité |
|-------------|-------------|---------------|-----|-------------|----------|
| **TechnicalAnalyst** system | 4 instructions : sépare faits/inférences, conditions validation/invalidation, n'invente pas | Anti-hallucination explicite, contrat de sortie 5 lignes | Pas de format JSON strict, sortie textuelle parsée | Migrer vers JSON strict avec schema validation | P2 |
| **TechnicalAnalyst** user | Variables interpolées (pair, RSI, MACD, ATR, trend, price) | Données structurées injectées | Pas de section "données manquantes" explicite | Ajouter `missing_data: []` pour traçabilité | P3 |
| **NewsAnalyst** system | 7 instructions : isoler catalyseurs, pas de causalité, distinguer no/weak/directional signal | Robuste, anti-surinterprétation | Prompt long (>500 tokens system), FX-spécifique (actif principal/référence) | Raccourcir, extraire logique FX dans un pré-filtre | P2 |
| **NewsAnalyst** user | Variables + headlines, contrat 5 lignes, règle FX séparée | Contrat de sortie explicite | Beaucoup de règles textuelles, pas de JSON strict | Migrer contrat vers JSON schema | P2 |
| **MarketContext** system | 4 instructions : régime, momentum, lisibilité, volatilité | Focalisé, clair | Très court (3 phrases) — peut manquer de cadrage | Ajouter contrainte de format JSON | P3 |
| **MarketContext** user | Variables techniques + contrat 5 lignes | Structuré | Contrat textuel, pas JSON | Aligner sur JSON comme Risk/Execution | P3 |
| **BullishResearcher** system | 4 instructions : thèse haussière, preuves, pas d'invention | Anti-hallucination | **Quasi-identique au BearishResearcher** (seul "haussière"→"baissière" change) | Fusionner en un seul prompt paramétré `{direction}` | P1 |
| **BearishResearcher** system | 4 instructions : thèse baissière, preuves, pas d'invention | Anti-hallucination | **Identique au Bullish** sauf direction | Fusionner | P1 |
| **TraderAgent** system | 2 instructions : résume justification, pas d'invention | Minimaliste | **Trop court** — ne cadre pas la logique de décision (c'est le code qui décide) | Acceptable car trader est LLM OFF par défaut | P3 |
| **RiskManager** user | Sortie JSON strict `{"decision":"APPROVE|REJECT","justification":"..."}` | JSON contract enforced | Bien structuré | — | — |
| **ExecutionManager** user | Sortie JSON strict `{"decision":"BUY|SELL|HOLD","justification":"..."}` | JSON contract enforced | Bien structuré | — | — |

### 4.3 Observations critiques sur les prompts

**Fait observé** (`agents.py:3268-3432` vs `3435-3599`) : Les classes `BullishResearcherAgent` et `BearishResearcherAgent` partagent **la même structure exacte**. Seuls changent :
- Le mot "haussière"/"baissière" dans le prompt
- Le target signal ('bullish'/'bearish') dans `_build_research_view()`

**Inférence** : Le débat bull/bear risque de produire des arguments **symétriques** car les prompts sont identiques en structure. Un chercheur haussier et un chercheur baissier avec le même template, les mêmes outils, et les mêmes données verront les mêmes patterns — seule la directive de direction diffère.

**Recommandation** : Paramétrer un seul `ResearcherAgent(direction='bullish'|'bearish')` et différencier les prompts au-delà de la simple direction (ex: le bearish devrait chercher les divergences, le bullish les confirmations).

### 4.4 Résistance aux hallucinations

| Mécanisme | Agent(s) | Preuve |
|-----------|----------|--------|
| "N'invente jamais" | Technical, News, Bullish, Bearish | Prompt system explicite |
| Contrat de sortie strict | Tous | User prompt avec format imposé |
| JSON strict | Risk, Execution | `{"decision":"APPROVE|REJECT"}` |
| Validation post-LLM | News | `_validate_news_output()` force neutral si pas d'évidence |
| Fallback déterministe | Tous | Si LLM OFF ou dégradé, score calculé sans LLM |
| Sign consistency | News | Score forcé positif si bullish, négatif si bearish |
| Side flip blocking | Execution | `same_side_confirmation` obligatoire |

**Fait observé** : La validation post-LLM est **bien implémentée** pour le NewsAnalyst (`_validate_news_output`, agents.py:1517-1630) mais **absente** pour les chercheurs bull/bear. Si le LLM bullish renvoie "bearish", le système ne corrige pas.

---

## 5. Analyse de la spécialisation des agents

### 5.1 Table : Clarté des rôles

| Agent | Rôle intentionnel | Rôle observé (code) | Chevauchement/conflit | Ajustement recommandé | Priorité |
|-------|-------------------|---------------------|----------------------|----------------------|----------|
| TechnicalAnalyst | Analyse indicateurs techniques | Score déterministe (trend±0.35, RSI±0.25, MACD±0.2), LLM bias optionnel 0.15 | Aucun, rôle distinct | Garder LLM OFF par défaut — valeur ajoutée LLM marginale ici | — |
| NewsAnalyst | Filtrage et scoring des news | Evidence weighting (relevance 0.62 + freshness 0.20 + credibility 0.18), LLM pour synthèse | Léger avec MarketContext sur macro events | Séparer clairement : News = micro-catalyseurs, Context = macro-régime | P3 |
| MarketContext | Régime de marché | Score déterministe (trend±0.12, momentum, EMA, RSI), régime 5 classes | Calculs RSI/EMA déjà faits par Technical → **duplication partielle** | Recevoir output Technical plutôt que recalculer | P2 |
| BullishResearcher | Thèse haussière | Agrège arguments bull, LLM pour debate text | **Identique en structure au Bearish** | Paramétrer un seul ResearcherAgent | P1 |
| BearishResearcher | Thèse baissière | Agrège arguments bear, LLM pour debate text | **Identique au Bullish** | Fusionner | P1 |
| TraderAgent | Décision finale | Scoring multi-source, contradiction detection, policy gating | Rôle clair et distinct — **c'est le cœur décisionnel** | Prompt trop court si LLM ON — enrichir | P3 |
| RiskManager | Validation risque | RiskEngine.evaluate() + LLM review optionnel | **LLM ne peut PAS override un rejet déterministe** — correct | Garder LLM OFF — apport marginal | — |
| ExecutionManager | Exécution ordre | JSON contract LLM + side confirmation gate | Side flip blocked — correct | Garder LLM OFF — logique suffisamment déterministe | — |

### 5.2 Valeur réelle de chaque agent dans la décision finale

**Fait observé** (`agents.py:3602-4441`, méthode `run()` du TraderAgent) :

Le TraderAgent calcule `combined_score` en pondérant :
- Technical analyst score (poids direct)
- News analyst score × coverage_multiplier (none=0%, low=35%, medium-high=100%)
- Market context score (poids direct)
- Debate score = `debate_sign * source_alignment * 0.12` (débat pèse ~12% max)
- Memory signal adjustment (±0.08 max)

**Inférence** : Le **débat bull/bear** a un impact de **±0.12 maximum** sur le score combiné. Comparé au technical analyst (±0.80) et au news analyst (±0.35 ajusté par coverage), le débat est un **facteur tertiaire**. Son coût en latence (2 appels LLM) est disproportionné par rapport à son influence.

**Hypothèse** : Le débat pourrait être remplacé par une agrégation déterministe des arguments, sans perte significative de qualité décisionnelle, avec un gain de latence de ~30-50%.

### 5.3 Duplication MarketContext ↔ TechnicalAnalyst

**Fait observé** :
- `TechnicalAnalystAgent.run()` calcule `trend_component ± 0.35` à partir de `market_snapshot['trend']`
- `MarketContextAnalystAgent.run()` recalcule `trend_component ± 0.12` à partir du **même** `market_snapshot['trend']`
- Les deux utilisent RSI, MACD, EMA du même snapshot

**Recommandation** : MarketContext devrait **recevoir** l'output Technical au lieu de recalculer. Sa valeur ajoutée est le **régime** (trending/ranging/calm/volatile/unstable) et le **contexte de session**, pas la re-lecture des mêmes indicateurs.

---

## 6. Analyse du runtime agentique

### 6.1 Valeur réelle du runtime

| Composant runtime | Valeur observée | Justification |
|-------------------|-----------------|---------------|
| Parallélisme Group 1 (3 agents) | **Haute** | Réduit latence de ~3x pour l'analyse initiale |
| Parallélisme Group 2 (2 researchers) | **Moyenne** | Gain modéré, mais le débat lui-même a un impact faible (±0.12) |
| Tool-calling loop (`_chat_with_runtime_tools`) | **Haute** | Permet aux agents LLM d'enrichir leur analyse dynamiquement |
| Second-pass | **Moyenne** | Améliore qualité sur cas marginaux, mais double la latence |
| Stagnation guard | **Haute** | Évite les boucles infinies, détection par 5 critères |
| Bundle selection (`_prefer_autonomy_bundle`) | **Haute** | Sélectionne le meilleur cycle parmi N |
| Memory refresh entre passes | **Moyenne** | Apport limité si embedding peu sémantique |
| Model override boost | **Basse** | Complexité additionnelle pour un gain incertain |

### 6.2 Table : Gouvernance outils

| Agent | Outils autorisés | Usage observé | Problème | Changement recommandé | Priorité |
|-------|-----------------|---------------|----------|----------------------|----------|
| technical-analyst | market_snapshot, indicator_bundle, divergence, S/R, patterns, MTF | `require_tool_call=True`, `default_tool_id='market_snapshot'` | Aucun — bien contraint | — | — |
| news-analyst | news_search, macro_feed, symbol_filter, sentiment | Tool loop avec LLM, circuit breaker (3 failures → 180s open) | news_search reçoit des items pré-chargés, pas d'appel API réel | Clarifier que c'est un scoring tool, pas un fetcher | P3 |
| market-context | regime, session, correlation, volatility | `require_tool_call=True`, `default_tool_id='market_regime_context'` | correlation_analyzer requiert secondary_closes rarement fourni | Vérifier si l'outil est réellement appelé ou toujours en fallback | P2 |
| bullish-researcher | evidence_query, thesis_support, scenario, memory | LLM-driven selection | Outils debate sont essentiellement des agrégateurs passthrough | Acceptable — outils structurent la réflexion | P3 |
| bearish-researcher | evidence_query, thesis_support, scenario, memory | Identique au bullish | Même observation | — | — |
| trader-agent | evidence, scenario, position_size, memory | Position_size_calculator **duplique** RiskEngine | **Duplication de sizing logic** | Supprimer position_size du trader, déléguer au Risk | P1 |
| risk-manager | scenario, position_size | RiskEngine.evaluate() est la vraie source | Position_size non utilisé si RiskEngine fait le sizing | Confirmer que RiskEngine est la seule source | P2 |
| execution-manager | scenario, position_size | JSON contract parsing | Outils rarement appelés (LLM OFF par défaut) | Acceptable | — |

### 6.3 Boucle tool-calling (`_chat_with_runtime_tools`)

**Fait observé** (`agents.py:703-941`) :
- `max_tool_rounds=2` par défaut
- `require_tool_call=True` force un appel outil même si le LLM n'en fait pas
- Fallback : si pas de tool_call, exécute `default_tool_id` automatiquement
- Filtrage kwargs : drops les arguments inconnus du handler (évite TypeError)

**Qualité** : Robuste. Le fallback tool_call évite les réponses LLM vides. Le filtrage kwargs protège contre les hallucinations de paramètres.

---

## 7. Analyse du contexte, de la mémoire et du cache

### 7.1 Table : Flux de contexte

| Flux | Contexte actuel | Problème | Stratégie recommandée | Bénéfice attendu |
|------|----------------|----------|----------------------|------------------|
| Technical → Debate | `_compact_analysis_outputs_for_debate()` : signal, score, reason, summary | Compaction correcte — **bon pattern** | — | — |
| News → Trader | Score pondéré par coverage (none=0%, low=35%) | Bon downweighting du bruit | — | — |
| Memory → Trader | `memory_signal` : direction, edge, risk_blocks, adjustments ±0.08 | **Embedding SHA256 sans sémantique** → recall limité | Migrer vers embedding pré-entraîné (sentence-transformers) | +30-50% recall précision |
| All → Trader | `analysis_outputs` complet (non compacté) | Contexte potentiellement large (tous les indicateurs raw) | Compacter aussi pour le trader | Réduction tokens LLM si trader LLM ON |
| Autonomy loop | Memory refresh avec limit_step croissant | **Pas de résumé intermédiaire** entre cycles | Ajouter un résumé du cycle précédent | Réduction contamination context |

### 7.2 Analyse de la mémoire vectorielle

**Fait observé** (`vector_memory.py`) :

**Embedding** : SHA256 hash des tokens et bigrams, projeté dans 64 dimensions.

```python
# Résumé de _embed():
digest = sha256(feature.encode('utf-8')).digest()
dim = int.from_bytes(digest[:2], byteorder='big') % 64
sign = 1.0 if (digest[2] % 2 == 0) else -1.0
values[dim] += sign * weight
```

**Problème** : Ce n'est **pas** un embedding sémantique. "EURUSD bullish breakout" et "EUR/USD haussier cassure" auront des embeddings **complètement différents** car les tokens sont hashés individuellement. Le seul rapprochement sémantique vient de l'alias map (`buy→bullish`, `sell→bearish`, `hold→neutral`), qui est très limité.

**Score composition** : `0.45 * vector + 0.38 * business + 0.17 * recency`
- Le score **business** (38%) compense partiellement la faiblesse du vector score en comparant RSI bucket, trend, MACD state, ATR bucket, etc.
- Le score **recency** (17%) ajoute un biais vers les mémoires récentes

**Outcome weighting** : `75% label_score + 25% RR_ratio` — bien conçu pour favoriser les mémoires gagnantes.

**Risk blocks** : `buy_risk_block si win_rate ≤ 0.20 ET avg_rr ≤ -0.20 ET count ≥ 3` — barrière déterministe contre la répétition d'erreurs.

**Verdict mémoire** : L'architecture est **bien conçue** (score multi-composant, outcome weighting, risk blocks) mais l'**embedding est le maillon faible**. La valeur réelle de la mémoire repose sur le score business (déterministe), pas sur la recherche vectorielle.

### 7.3 Mémoire Memori

**Fait observé** (`memori_memory.py`, `config.py`) : `MEMORI_ENABLED=False` par défaut. Service Memori est un backend alternatif (graphe sémantique) mais non activé en production. La recall fonctionne mais n'est pas intégrée dans le pipeline principal sauf si explicitement activée.

---

## 8. Analyse de la frontière LLM vs déterministe

### 8.1 Table complète

| Composant/Flux | Mode actuel | Problème | Mode recommandé | Raison | Priorité |
|----------------|------------|----------|-----------------|--------|----------|
| TechnicalAnalyst scoring | Déterministe (LLM OFF) | Aucun | **Garder déterministe** | Score (trend±0.35, RSI±0.25, MACD±0.2) est précis et reproductible | — |
| TechnicalAnalyst LLM bias | LLM optionnel (bias 0.15) | LLM bias marginal (10% blend) | Garder optionnel | Bon ratio coût/valeur quand activé | — |
| NewsAnalyst evidence scoring | Déterministe (relevance*0.62 + freshness*0.20 + credibility*0.18) | Aucun | **Garder déterministe** | Formule pondérée robuste | — |
| NewsAnalyst LLM summary | LLM ON | LLM tokens limités (96 premier appel, 384 retry) | Garder LLM pour synthèse narrative | Valeur ajoutée pour explicabilité | — |
| MarketContext regime | Déterministe (ATR ratio, slope) | Aucun | **Garder déterministe** | Régime calculé sans ambiguïté | — |
| Bullish/Bearish debate | **LLM ON** | **Impact faible (±0.12) vs coût (2 appels LLM)** | Évaluer remplacement par agrégation déterministe | Ratio coût/valeur défavorable | P1 |
| TraderAgent decision | **Déterministe** (LLM OFF default) | Aucun — **excellent choix** | **Garder déterministe** | Décision reproductible, policy-gated | — |
| TraderAgent LLM note | LLM optionnel pour rationale | Validation de cohérence post-LLM | Garder optionnel | Explicabilité | — |
| RiskEngine.evaluate() | **100% déterministe** | Aucun | **Ne jamais migrer vers LLM** | Barrière de sécurité critique | — |
| RiskManager LLM review | LLM OFF default, LLM **ne peut pas** override rejet déterministe | Correct — LLM en lecture seule | Garder | Architecture sûre | — |
| ExecutionManager JSON | LLM pour confirmation side | Side flip **bloqué** même si LLM le demande | Correct | Sécurité d'exécution | — |
| JSON schema validation | **Absent** comme layer séparé | JSON parsé inline avec fallback HOLD | Ajouter validation JSON schema formelle | Robustesse accrue | P2 |
| SL/TP geometry | Déterministe (`validate_sl_tp_update`) | Correct | Garder | Pas de LLM dans les niveaux de prix | — |
| Position sizing | **Dupliqué** : MCP `position_size_calculator` + `RiskEngine.evaluate()` | Deux sources de vérité potentiellement divergentes | **Unifier** : MCP tool délègue au RiskEngine | Cohérence | P1 |
| Live-trade gate | Déterministe (`_is_live_trade_candidate`) | Correct — 4 conditions vérifiées | Garder | Pas de LLM dans le gate | — |
| Tool allowlist | Déterministe (`_run_agent_tool` + `enabled_tools`) | Correct | Garder | Gouvernance fiable | — |

### 8.2 Le RiskEngine est-il une vraie barrière ?

**Fait observé** (`agents.py:4444-4602`) :
```python
# RiskManagerAgent.run():
risk_eval = self.risk_engine.evaluate(mode, decision, risk_percent, price, stop_loss, pair)
# LLM review:
llm_approved = parsed_json.get('decision') == 'APPROVE'
# Final:
final_accepted = risk_eval.accepted AND llm_approved  # (si LLM ON)
# Mais si LLM OFF:
final_accepted = risk_eval.accepted  # ← déterministe seul
```

**Fait observé** (`engine.py:1485-1514`) : L'exécution ne se déclenche que si `execution_plan['should_execute'] AND side in {'BUY', 'SELL'}`.

**Fait observé** (`engine.py:294-310`) : `_is_live_trade_candidate` exige `decision in {BUY,SELL} AND execution_allowed AND risk_accepted AND volume > 0`.

**Verdict** : Le RiskEngine est une **vraie barrière** — pas une validation cosmétique. Avec LLM OFF (default), c'est la **seule** source de vérité pour l'acceptation du risque. Avec LLM ON, le LLM peut *ajouter* un rejet mais ne peut **jamais** forcer une acceptation que le déterministe a rejetée.

---

## 9. Analyse de la logique trading multi-produit

### 9.1 Table : Flux de décision

| Flux de décision | Logique actuelle | Risque/Faiblesse | Amélioration recommandée | Priorité |
|-----------------|-----------------|------------------|-------------------------|----------|
| Signal → Score | Weighted sum : tech + news*coverage + context | **News coverage=none → 0%** : ignore les news même si elles existent mais sont non scorées | Distinguer "pas de news" vs "news non pertinentes" | P2 |
| Source alignment | `(aligned - opposing) / total * coverage_factor * independence_factor` | Facteurs multiplicatifs peuvent se combiner de manière opaque | Logger les facteurs intermédiaires pour audit | P3 |
| Contradiction detection | `macd_atr_ratio` : major ≥0.12, moderate ≥0.05, weak >0 | **macd_atr_ratio** dépend de l'échelle de l'instrument — crypto BTC (ATR ~1000) vs forex (ATR ~0.005) | Normaliser par asset class ou utiliser le ratio price-relative | P2 |
| Memory risk block | `win_rate ≤ 0.20 AND avg_rr ≤ -0.20 AND count ≥ 3` | **Seuil count=3** est bas — peut bloquer sur un échantillon insuffisant | Augmenter à count≥5 pour significativité | P2 |
| SL/TP calculation | `SL = price ± ATR*1.5, TP = price ± ATR*2.5` | **Risk/reward = 2.5/1.5 ≈ 1.67** — acceptable mais fixe | Paramétrer R:R par asset class (crypto = plus large) | P3 |
| Decision gating | 3 modes (conservative: score≥0.30, balanced: ≥0.25, permissive: ≥0.12) | Conservative mode strictement paramétré | Bien conçu — pas de changement | — |
| Debate balance | `debate_score = debate_sign * source_alignment * 0.12` | **Impact maximal de ±0.12** — faible vs technical (±0.80) | Si debate_score doit avoir plus d'impact, augmenter le coefficient | P3 |
| HOLD decision | Si `!minimum_evidence_ok OR !quality_gate_ok` | HOLD est **le défaut sûr** — correct | — | — |

### 9.2 Cohérence multi-produit dans le sizing

**Fait observé** (`rules.py`) :

| Asset Class | pip_size | pip_value/lot | contract_size | min/max volume |
|-------------|----------|---------------|---------------|----------------|
| forex | 0.0001 (JPY: 0.01) | 10.0 | 100K | 0.01-10.0 |
| crypto | Adaptive (0.0001→1.0) | 1.0 | 1 | 0.001-100.0 |
| index | 1.0 | 1.0 | 1 | 0.1-50.0 |
| metal | 0.01 | 10.0 | 100 | 0.01-10.0 |
| energy | 0.01 | 10.0 | 1000 | 0.01-10.0 |
| equity | 0.01 | 1.0 | 1 | 1.0-1000.0 |

**Fait observé** : MCP `position_size_calculator` a ses **propres** specs :
- forex max_volume=10 (vs RiskEngine max=10.0) ✓
- crypto max_volume=100 (vs RiskEngine max=100.0) ✓
- equity max_volume=1000 (vs RiskEngine max=1000.0) ✓

**Les specs sont alignées actuellement**, mais cette duplication est un risque de divergence future.

### 9.3 Margin estimation

**Fait observé** (`rules.py`) : `margin_required = volume * contract_size * price / 100` — assume leverage 1:100 **hardcodé**. Pas de paramètre leverage configurable.

**Risque** : Leverage varie par instrument et broker. Un equity à leverage 1:5 sera sous-estimé en marge de 20x.

---

## 10. Analyse du risk management et de l'exécution

### 10.1 Chaîne de validation complète

```
TraderAgent.run()
  → decision = BUY/SELL/HOLD (déterministe, policy-gated)
  → entry, stop_loss, take_profit (ATR-based)
  → volume_multiplier (contradiction-adjusted)
    ↓
RiskManagerAgent.run()
  → RiskEngine.evaluate(mode, decision, risk_percent, price, stop_loss)
    → pip_size, pip_value, volume limits (asset-class-aware)
    → suggested_volume = risk_amount / (sl_pips * pip_value)
    → volume clamped [min, max]
    → risk_percent checked vs mode limits (sim:5%, paper:3%, live:2%)
    → stop_distance >= 0.05% minimum
  → LLM review (optionnel, ne peut pas override rejet)
  → final_accepted = deterministic AND llm_approved
    ↓
ExecutionManagerAgent.run()
  → JSON contract: {"decision":"BUY|SELL|HOLD"}
  → same_side_confirmation obligatoire
  → side flip → HOLD (bloqué)
  → degraded LLM → HOLD
    ↓
_is_live_trade_candidate()
  → decision in {BUY,SELL} AND execution_allowed AND risk_accepted AND volume > 0
    ↓
Live mode degradation check
  → Si agent critique dégradé → RuntimeError (abort)
    ↓
ExecutionService.execute()
  → MetaAPI order (live/paper) ou simulation log
```

### 10.2 Protection contre les décisions faibles

| Protection | Implémentation | Preuve |
|-----------|---------------|--------|
| Score minimum | `min_combined_score` par policy (0.12-0.30) | `agents.py:1068-1135` DECISION_POLICIES |
| Confidence minimum | `min_confidence` par policy (0.22-0.35) | Idem |
| Sources alignées minimum | `min_aligned_sources` (1-2) | Idem |
| Major contradiction block | `block_major_contradiction=True` en conservative | Idem |
| Memory risk block | `buy/sell_risk_block` si historique perdant | `vector_memory.py` |
| Volume multiplier | Contradiction penalty : major → volume×0.45-0.55 | `agents.py:1089-1098` |
| Live mode 2% max risk | `mode=='live' → max 2.0%` | `rules.py` evaluate() |
| Stop distance minimum | `≥ 0.05% du prix` | `rules.py` evaluate() |

---

## 11. Analyse des modes de défaillance

| Composant/Flux | Mode de défaillance | Cause | Impact | Mitigation recommandée | Priorité |
|---------------|--------------------|----|--------|----------------------|----------|
| LLM provider | Timeout/503 | Serveur Ollama/OpenAI down | Agent dégradé → HOLD | Circuit breaker (existe pour News), étendre à tous | P2 |
| LLM response | JSON invalide | Hallucination de format | Risk/Execution → HOLD (fallback) | Ajouter JSON schema validation formelle (jsonschema) | P2 |
| LLM response | Surconfiance | LLM affirme avec certitude sans données | Score gonflé | Validation post-LLM (existe pour News, **absente pour Researchers**) | P1 |
| LLM response | Contradiction direction | LLM bullish dit "bearish" | Incohérence | Vérifier cohérence LLM signal vs prompted direction | P2 |
| MCP tool | Exception | Données manquantes/invalides | Tool returns error dict | Fallback implémenté dans langchain_tools.py wrappers — correct | — |
| MetaAPI | Timeout | Broker API down | Pas de données marché | Circuit breaker 20s + yfinance fallback — correct | — |
| Qdrant | Indisponible | Service down | Mémoire ignorée | Analyse continue sans mémoire — correct | — |
| Memory | Mémoire obsolète | Conditions marché changées | Signal mémoire incorrect | risk_blocks limités à 3+ trades + score ±0.08 max — **acceptable** | — |
| Concurrent runs | Double exécution | 2 runs même pair simultanés | Double position | **Aucun mutex** — risque réel | P1 |
| Position sizing | Divergence MCP ↔ RiskEngine | Specs désynchronisées | Volume incorrect | **Unifier** les sources | P1 |
| Stagnation | Boucle autonomy | Même output entre cycles | Latence gaspillée | Stagnation guard (5 critères) — correct | — |
| Live degradation | Agent critique dégradé | LLM partiel failure | Trade non exécuté | RuntimeError abort — correct | — |

---

## 12. Analyse de l'observabilité

### 12.1 Métriques présentes

| Métrique | Type | Labels | Couverture |
|----------|------|--------|-----------|
| `analysis_runs_total` | Counter | status | Runs complétés/échoués |
| `orchestrator_step_duration_seconds` | Histogram | agent | Latence par agent |
| `mcp_tool_calls_total` | Counter | tool, status | Appels outils MCP |
| `mcp_tool_duration_seconds` | Histogram | tool, status | Latence outils |
| `agentic_runtime_runs_total` | Counter | — | Runs agentic V2 |
| `agentic_runtime_tool_calls_total` | Counter | tool | Outils runtime |
| `agentic_runtime_final_decisions_total` | Counter | decision | BUY/SELL/HOLD |
| `agentic_runtime_execution_outcomes_total` | Counter | outcome | Exécutions |
| `risk_evaluation_total` | Counter | accepted, asset_class, mode | Évaluations risque |
| LLM call log (DB) | Table | provider, model, tokens, cost, latency | Chaque appel LLM |

### 12.2 Métriques manquantes

| Métrique manquante | Pourquoi elle est importante | Priorité |
|-------------------|---------------------------|----------|
| `debate_impact_score` | Mesurer l'impact réel du débat bull/bear sur la décision | P1 |
| `memory_recall_quality` | Mesurer la précision de la mémoire (hits pertinents / total) | P2 |
| `contradiction_detection_total` | Fréquence des contradictions (major/moderate/weak) | P2 |
| `decision_gate_blocking_total` | Quel gate bloque le plus souvent (score/confidence/sources) | P2 |
| `llm_token_waste_ratio` | Tokens consommés pour un HOLD final vs coût total | P2 |
| `autonomy_second_pass_improvement` | Score delta entre cycle 1 et cycle final | P2 |
| `prompt_template_version` | Quelle version de prompt est active par agent | P3 |

### 12.3 Debug traces

**Fait observé** : 13 traces JSON enregistrées dans `debug-traces/` (jusqu'à 439 KB). Format structuré avec `schema_version`, `run`, `context`, `workflow`, `agent_steps`, `analysis_bundle`, `final_decision`. C'est un **excellent** mécanisme de diagnostic mais il est désactivé par défaut (`debug_trade_json=False`).

---

## 13. Analyse de la qualité des sorties

### 13.1 Contrats de sortie par agent

| Agent | Format sortie | Validation | Robustesse |
|-------|--------------|-----------|-----------|
| TechnicalAnalyst | Dict structuré (signal, score, indicators, structure) | Score clamped [-1,1], signal enum vérifié | **Haute** — déterministe |
| NewsAnalyst | Dict structuré (signal, score, evidence, coverage) | `_validate_news_output()` : force neutral, sign consistency, score compression | **Haute** — validé post-LLM |
| MarketContext | Dict structuré (signal, score, regime, momentum) | Score clamped [-0.35, 0.35], regime enum | **Haute** — déterministe |
| BullishResearcher | Dict (arguments, confidence, counter_args, invalidation) | Pas de validation post-LLM | **Moyenne** — LLM non contraint |
| BearishResearcher | Dict (arguments, confidence, counter_args, invalidation) | Pas de validation post-LLM | **Moyenne** — LLM non contraint |
| TraderAgent | Dict (decision, confidence, combined_score, entry, SL, TP, gates) | Decision enum, gates list, score bounds | **Haute** — déterministe |
| RiskManager | Dict (accepted, reasons, suggested_volume) | RiskEngine + optional LLM JSON | **Haute** — déterministe barrière |
| ExecutionManager | Dict (decision, should_execute, side, volume) | JSON contract strict, side confirmation | **Haute** — side flip blocked |

### 13.2 Risques de sortie

| Risque | Agent(s) | Fréquence estimée | Impact |
|--------|---------|-------------------|--------|
| Score hors bornes | Aucun (clamped) | Nulle | — |
| Signal incohérent avec score | NewsAnalyst | Rare (sign consistency enforced) | Faible |
| Arguments inventés | Bull/Bear Researchers | **Possible si LLM hallucine** | Moyen — le trader ne se fie qu'au score, pas aux arguments textuels |
| JSON malformé | Risk/Execution LLM | Rare mais possible | Faible — fallback HOLD |
| SL/TP aberrants | TraderAgent | Rare (ATR-based) | Moyen — RiskEngine vérifie distance minimum |

---

## 14. Plan de tests d'intégration

| Test | Scope | Dépendances | Résultat attendu | Priorité |
|------|-------|------------|------------------|----------|
| Pipeline complet simulation EURUSD | 8 agents, MCP tools, RiskEngine | Mock LLM, mock market data | Decision BUY/SELL/HOLD + trace complète | P0 |
| Pipeline complet BTCUSD (crypto) | Multi-produit, adaptive pip sizing | Mock LLM, mock market data | pip_size=1.0, asset_class='crypto' | P0 |
| Pipeline AAPL (equity) | Equity sizing, min_volume=1.0 | Mock LLM, mock market data | volume ≥ 1.0, pip_size=0.01 | P1 |
| Gouvernance outil interdit | Agent appelle outil hors allowlist | Mock LLM | Tool disabled, agent fallback | P0 |
| Risk rejet → HOLD propagé | risk_percent=5% en live | Aucun mock | RiskEngine reject, ExecutionManager HOLD | P0 |
| LLM dégradé → HOLD | LLM timeout | Mock LLM raise TimeoutError | Agents degraded, final HOLD | P0 |
| Contradiction majeure → HOLD | Trend bullish + MACD strongly bearish | Mock market data | `major_contradiction_block=True`, HOLD | P1 |
| Memory risk block → HOLD | 3+ trades perdants sur même pair | Qdrant avec mémoires mock | `risk_blocks.buy=True`, HOLD | P1 |
| Second-pass amélioration | Cycle 1 HOLD → cycle 2 BUY | Mock LLM, memory refresh | `selected_cycle=2`, decision BUY | P1 |
| Stagnation guard | 2 cycles identiques | Mock LLM même output | `stagnation_guardrail`, stop rerun | P1 |
| Live mode abort degraded | Agent critique dégradé en live | Mock LLM degraded | RuntimeError raised | P0 |
| Side flip blocked | LLM execution dit SELL quand trader dit BUY | Mock LLM | HOLD final (flip blocked) | P1 |

---

## 15. Plan de tests E2E

| Test | Scope | Dépendances | Résultat attendu | Priorité |
|------|-------|------------|------------------|----------|
| API → Celery → Orchestrateur → DB | Full stack docker | PostgreSQL, Redis, RabbitMQ | Run complété, status='completed' en DB | P0 |
| Celery queue → worker → websocket notification | Task queue lifecycle | Redis, RabbitMQ | WS message reçu par client | P1 |
| MetaAPI indisponible → circuit breaker → yfinance fallback | Market data fallback chain | Mock MetaAPI (503) | Analysis complétée avec données yfinance | P1 |
| Qdrant indisponible → analyse dégradée | Memory degradation | Qdrant down | Analysis complétée sans mémoire, `memory_signal.used=False` | P1 |
| Run live refusé → risk check | Live gate | risk_percent > 2% | Run status='completed', decision=HOLD | P0 |
| Concurrent runs même pair | Race condition | 2 tasks simultanées | Pas de double position (à implémenter) | P1 |

---

## 16. Plan d'évaluation et de performance

| Scénario | Composant cible | Métrique | Profil de charge | Critère de succès | Priorité |
|----------|----------------|---------|-----------------|-------------------|----------|
| Latence pipeline complet | Orchestrateur | P95 latency | 1 run, simulation | < 30s (Ollama local), < 15s (LLM OFF) | P0 |
| Latence par agent | Chaque agent | P95 latency | 1 run | Technical < 2s, News < 5s, Trader < 3s | P1 |
| Latence MCP tool | 19 tools | P99 latency | 100 appels | < 50ms chacun | P1 |
| Impact second-pass | Autonomy loop | Score delta cycle1→cycleN | 20 runs varied | Score improvement > 0.05 dans > 30% des cas | P1 |
| Impact débat bull/bear | Researchers | debate_score contribution | 50 runs | debate_score > 0.06 dans > 40% des cas | P1 |
| Contexte long vs court | Agent prompts | Token count + latency | Même scénario, contexte ±50% | Latence proportionnelle, qualité stable | P2 |
| Charge concurrente | Celery workers | Throughput runs/min | 10 runs parallèles | > 5 runs/min complétés | P2 |
| Coût LLM par run | LLM calls | Total tokens + cost_usd | 20 runs variés | < 0.05$ par run (Ollama), < 0.50$ (OpenAI) | P1 |
| Coût marginal agent supplémentaire | LLM layer | Token delta | Avec/sans researchers | Quantifier le coût des researchers | P2 |
| Memory search performance | VectorMemoryService | P95 latency | 1000 entries, 10 queries | < 50ms par query | P2 |

---

## 17. Top bottlenecks

| # | Bottleneck | Impact | Preuve | Remédiation |
|---|-----------|--------|--------|-------------|
| 1 | **Débat bull/bear : coût élevé, impact faible** | 2 appels LLM pour ±0.12 max sur combined_score | `agents.py:3602` — `debate_score = debate_sign * source_alignment * 0.12` | Évaluer remplacement par agrégation déterministe |
| 2 | **Embedding SHA256 sans sémantique** | Recall mémoire limité aux correspondances lexicales exactes | `vector_memory.py:_embed()` — hash-based, pas de sémantique | Migrer vers sentence-transformers (même 384-dim) |
| 3 | **Position sizing dupliqué** | Risque de divergence MCP tool vs RiskEngine | `mcp_trading_server.py` position_size_calculator + `rules.py` evaluate() | Faire déléguer le MCP tool au RiskEngine |
| 4 | **agents.py = 4 773 lignes** | Maintenabilité, revue de code, testing isolé | Fichier unique avec 8 classes + helpers | Extraire chaque agent dans son module |
| 5 | **Pas de mutex concurrent runs** | Double position sur même pair possible | Aucun mécanisme observé | Ajouter lock par pair (Redis ou DB) |

---

## 18. Quick wins

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 1 | Ajouter validation post-LLM pour Researchers (cohérence direction) | 2h | Réduction hallucinations débat |
| 2 | Logger `debate_impact_score` en métrique Prometheus | 1h | Mesure de la valeur réelle du débat |
| 3 | Ajouter `contradiction_detection_total` metric | 1h | Visibilité sur la fréquence des contradictions |
| 4 | Renommer `forex.db` → `trading.db`, `forex_long_term_memory` → `trading_memory` | 30min | Cohérence naming multi-produit |
| 5 | Paramétrer leverage par asset class au lieu de hardcoder 1:100 | 1h | Margin estimation correcte pour equities |
| 6 | Activer `debug_trade_json=True` en simulation/paper par défaut | 5min | Diagnostic facilité |
| 7 | Ajouter test E2E pipeline complet avec mock LLM | 4h | Couverture flux critique manquante |

---

## 19. Recommandations prioritaires

### P0 — Critique

1. **Unifier position sizing** : Faire déléguer `position_size_calculator` MCP au `RiskEngine.evaluate()` pour éliminer la duplication et le risque de divergence
2. **Ajouter mutex concurrent runs** : Implémenter un lock Redis par pair pour éviter les doubles positions

### P1 — Haute

3. **Évaluer le ratio coût/valeur du débat bull/bear** : Mesurer sur 50 runs si `debate_score > 0.06` dans plus de 40% des cas. Si non, remplacer par agrégation déterministe
4. **Fusionner BullishResearcher et BearishResearcher** en un seul `ResearcherAgent(direction)` avec prompts différenciés au-delà de la simple inversion de direction
5. **Migrer embedding mémoire** vers un modèle pré-entraîné (sentence-transformers, dimension 384+) pour un recall sémantique réel
6. **Ajouter validation post-LLM pour Researchers** : Vérifier que le signal retourné par le LLM correspond à la direction demandée

### P2 — Moyenne

7. **Normaliser contradiction detection par asset class** : `macd_atr_ratio` doit être relatif au prix pour être comparable entre forex et crypto
8. **Extraire chaque agent dans son propre fichier** : Réduire `agents.py` de 4 773 lignes à 8 fichiers de ~500 lignes
9. **Activer OpenTelemetry** pour distributed tracing
10. **Paramétrer leverage** par instrument/broker au lieu du hardcode 1:100

### P3 — Basse

11. **MarketContext** : Recevoir output TechnicalAnalyst au lieu de recalculer les mêmes indicateurs
12. **Migrer contrats de sortie textuels** vers JSON schema strict pour Technical/News/Context
13. **Ajouter alerting Grafana** basé sur les métriques existantes

---

## 20. Décisions d'architecture recommandées

### Questions posées et réponses

| Question | Réponse | Preuve |
|----------|---------|--------|
| Les prompts sont-ils assez précis et contrôlables ? | **Oui pour Risk/Execution (JSON strict), partiellement pour les autres (contrat textuel)** | `agents.py:4444` JSON contract, `agents.py:1633` textual contract |
| Les rôles sont-ils bien séparés ? | **Oui sauf Bull/Bear (identiques) et MarketContext/Technical (duplication partielle)** | `agents.py:3268` vs `3435` (même structure), `agents.py:2772` vs `1633` (mêmes indicateurs) |
| Le runtime apporte-t-il une vraie valeur ? | **Oui : parallélisme, stagnation guard, bundle selection. Le second-pass a une valeur marginale** | `engine.py:1064-1239` parallel groups, `engine.py:1382` stagnation |
| Le débat bull/bear améliore-t-il la décision ? | **Impact observable limité à ±0.12 sur combined_score. À mesurer empiriquement** | `agents.py:3602` — `debate_score = debate_sign * alignment * 0.12` |
| La mémoire améliore-t-elle la décision ? | **Architecture bien conçue (outcome weighting, risk blocks) mais embedding trop faible pour un recall sémantique réel** | `vector_memory.py:_embed()` SHA256 hash, `compute_memory_signal()` risk_blocks |
| Le RiskEngine est-il la vraie source de vérité ? | **Oui, 100%. Le LLM ne peut pas override un rejet déterministe** | `agents.py:4500` — `final_accepted = risk_eval.accepted AND llm_approved` |
| Y a-t-il des duplications de logique ? | **Oui : position_size_calculator MCP duplique RiskEngine, MarketContext recalcule les indicateurs de Technical** | `mcp_trading_server.py` position_size vs `rules.py` evaluate(), `agents.py:2772` vs `1633` |
| L'exécution est-elle protégée ? | **Oui : side flip blocked, degraded→HOLD, live abort, JSON contract strict, 4 conditions live gate** | `agents.py:4605-4773` side confirmation, `engine.py:294-310` live gate |
| Le système est-il production-ready ? | **Simulation/paper : oui. Live : conditions nécessaires remplies (risk gate, abort, degraded mode) mais mutex manquant et naming incohérent** | Pas de lock concurrent + `forex.db` naming |

---

## 21. Modifications réellement effectuées (session précédente + actuelle)

| # | Fichier(s) | Modification | Type |
|---|-----------|-------------|------|
| 1 | 15 fichiers `db/models/*.py` | `datetime.utcnow` → `datetime.now(timezone.utc)` | Correction |
| 2 | 4 fichiers services/routes | Idem | Correction |
| 3 | `mcp_client.py` | Ajout `TOOL_ID_ALIASES`, alias resolution dans `build_tool_specs()`, `call_tool()`, `has_tool()` | Refactoring |
| 4 | `Chart.yaml` | `forex-platform` → `trading-platform` | Généricisation |
| 5 | 4 fichiers racine vides | Suppression fichiers parasites (`agent,`, `décision`, etc.) | Nettoyage |
| 6 | `test_mcp_client_alias.py` (nouveau) | 12 tests alias resolution + gouvernance | Tests |
| 7 | `test_risk_engine_multiproduct.py` (nouveau) | 14 tests multi-produit (forex/crypto/index/metal/equity/SL-TP) | Tests |

---

## 22. Tests réellement exécutés

```
Commande : .venv/bin/python -m pytest tests/unit/ --tb=short
Résultat : 357 passed, 3 warnings in 8.58s
```

**Détail** : 331 tests originaux + 26 nouveaux tests ajoutés. Les 3 warnings sont des `DeprecationWarning` de `SwigPyPacked`/`SwigPyObject` dans la bibliothèque C du client Qdrant (hors périmètre projet).

---

## 23. Verdict final

### Forces architecturales

1. **Frontière LLM/déterministe exemplaire** : Les 4 agents les plus critiques (Technical, Trader, Risk, Execution) sont LLM OFF par défaut. Le RiskEngine est une barrière infranchissable par le LLM. C'est l'un des meilleurs designs observés dans un système de trading AI.

2. **Logique de décision trading sophistiquée** : 3 politiques décisionnelles, détection de contradictions multi-niveaux, memory risk blocks, source alignment scoring. Le système rejette correctement les setups faibles.

3. **Gouvernance outils robuste** : enabled_tools enforced au runtime, alias resolution, double-check canonical/alias. Aucun agent ne peut appeler un outil non autorisé.

4. **Mémoire outcome-weighted** : L'idée de pondérer les mémoires par le résultat réel des trades (win/loss/RR) est architecturalement excellente, même si l'embedding limite le recall.

### Faiblesses à corriger

1. **Débat bull/bear sous-optimisé** : Coût élevé (2 appels LLM), impact faible (±0.12), prompts identiques. Ratio coût/valeur à valider empiriquement.

2. **Embedding mémoire non sémantique** : SHA256 hash ne capture pas la sémantique. Le score business (38%) compense partiellement mais le recall reste limité.

3. **Duplication position sizing** : Deux sources de vérité potentiellement divergentes.

4. **Pas de mutex concurrent** : Risque de double position en production.

### Score final : 3.63/5

Architecture bien conçue avec une séparation LLM/déterministe parmi les meilleures du domaine. Les corrections prioritaires concernent la duplication de sizing, le mutex concurrent, et la validation du ratio coût/valeur du débat bull/bear. Le système est **production-ready pour simulation et paper trading**, et **conditionnellement ready pour live** une fois le mutex et le naming corrigés.
