# Revue IA Prompt Architecture & Trading - Tracker d'avancement

Date de mise a jour: 2026-03-17  
Projet: `forex-multiagent-platform-ai-prompt-architecture-and-trading-expert-review-001`

## Legende statuts

- `A_FAIRE`
- `EN_COURS`
- `TERMINE`
- `BLOQUE`

## Resume executif

- L'audit architecture IA/prompt/trading est termine.
- Le workflow multi-agent est bien structure et trace. Le lot 1 P0 a renforce l'impact du debat bullish/bearish dans la decision trader.
- Les points critiques pour un usage live auto restent ouverts (notamment agregation decisionnelle et sizing risque).
- L'etat recommande est:
  - `GO` en simulation/paper controle
  - `NO_GO` en live automatique tant que les actions P0 ne sont pas traitees.

## Etat global

| Bloc | Statut | Commentaire |
|---|---|---|
| Audit code/prompts/workflows/docs | TERMINE | lecture complete backend/frontend/docs |
| Validation technique locale | TERMINE | backend tests passes + frontend build OK |
| Plan de remediation priorise | TERMINE | P0/P1/P2 formalises |
| Mise en oeuvre des correctifs P0 | EN_COURS | lot 1 applique (3 quick wins P0 sur 6) |
| Validation post-correctifs (integration/perf) | A_FAIRE | depend des correctifs P0/P1 |

## Validation realisee

- Backend tests: `85 passed` (`backend pytest -q`) - run apres correctifs P0 lot 1
- Frontend build: `OK` (`frontend npm run build`) - run apres correctifs P0 lot 1
- Donnees runtime locales pour validation metier:
  - `analysis_runs=0`
  - `backtest_runs=0`
  - `llm_call_logs=0`
  - `memory_entries=0`

## Etat d'avancement correctifs P0

| Stream P0 | Statut | Avancement |
|---|---|---|
| Determinisme risk/execution (backend + API + UI) | TERMINE | model selector force OFF + sanitization API + switch UI verrouille |
| Decision trader debate-aware | TERMINE | `debate_score` et `combined_score` integres avec garde-fou conflit |
| Contrat execution unifie | TERMINE | `status/executed/reason` normalises dans `ExecutionService` |
| JSON strict sorties agents | A_FAIRE | schemas et validation forte encore non implementes |
| Sizing instrument-aware | A_FAIRE | moteur risque encore base sur hypotheses simplifiees |
| Specialisation macro/sentiment | A_FAIRE | overlap encore present |

## Avancement par axe obligatoire

| Axe | Statut | Observation cle |
|---|---|---|
| prompt-architecture-review | TERMINE | prompts utilisables mais peu contraints (sorties non strictes) |
| agent-specialization-review | TERMINE | chevauchement macro/sentiment + debat peu discriminant |
| context-and-memory-review | TERMINE | contexte partiellement redondant, memoire non semantique |
| reasoning-and-debate-review | TERMINE | lot 1: `debate_score` injecte; calibration/validation complementaires restantes |
| trading-logic-review | TERMINE | logique nette mais simplifiee (seuils fixes, sizing simplifie) |
| llm-usage-review | TERMINE | risk/execution desormais deterministes; optimisation restante sur agents analytiques/debat |
| output-quality-review | TERMINE | structures heterogenes, schema strict manquant par agent |
| integration-test-review | TERMINE | plan defini, couverture e2e reelle encore insuffisante |
| evaluation-and-performance-review | TERMINE | metriques/scenarios proposes, campagne non executee |

## Decisions d'architecture (reponses)

| Question | Reponse |
|---|---|
| Prompts assez precis pour robustesse/stabilite ? | Non, partiellement seulement |
| Roles agents bien separes ? | Partiellement, redondances presentes |
| Contexte injecte adapte ? | Mal hierarchise selon les etapes |
| Plus de memoire ou moins ? | Mieux filtree, plus semantique, pas forcement plus volumineuse |
| Debat multi-agent utile ? | Valeur limitee actuellement |
| Decision finale trading exploitable ? | Oui en simulation/paper prudent, insuffisant pour live auto |
| Quels agents LLM-driven ? | news, bullish/bearish, schedule planner, order guardian (reporting) |
| Quels traitements deterministes ? | technical core, risk, execution, noyau de decision trader |

## Top bottlenecks

| Rang | Bottleneck | Impact | Statut |
|---|---|---|---|
| 1 | Debat bullish/bearish peu injecte dans la decision trader | qualite decisionnelle | TERMINE (lot 1) |
| 2 | Parsing lexical LLM (non structure) | robustesse/hallucinations | A_FAIRE |
| 3 | Sizing risque simplifie (pip fixe) | risque metier | A_FAIRE |
| 4 | Macro/sentiment redondants | cout/latence/valeur | A_FAIRE |
| 5 | Memoire non semantique | pertinence contexte | A_FAIRE |
| 6 | Contrats JSON non stricts | fiabilite integration | EN_COURS (contrat execution normalise) |

## Quick wins

| Action | Gain attendu | Priorite | Statut |
|---|---|---|---|
| Imposer JSON strict sur agents analytiques/debat/trader | stabilite sorties | P0 | A_FAIRE |
| Integrer un `debate_score` dans la decision trader | meilleure coherence | P0 | TERMINE |
| Normaliser contrat execution (`status`,`executed`,`reason`) | lisibilite pipeline | P0 | TERMINE |
| Rendre risk/execution non-switchables LLM en UI/API | reduction non-determinisme | P0 | TERMINE |
| Filtrer les news par recence avant prompt | reduction bruit | P1 | A_FAIRE |
| Ajouter tests de reponses LLM invalides | resilience | P1 | A_FAIRE |

## Tableau: agent_prompt_review

| agent_or_prompt | current_usage | best_practice | gap | optimization_opportunity | priority |
|---|---|---|---|---|---|
| technical-analyst | texte libre + parse mots-cles | JSON strict schema valide | fragilite linguistique | schema + validation + fallback HOLD | P0 |
| news-analyst | sentiment parse + score fixe | confidence exploitee | perte d'info | extraire confidence structuree | P1 |
| bullish/bearish prompts | contexte quasi identique des 2 cotes | debat contradictoire structure | redondance | format claim/rebuttal/invalidation | P0 |
| trader-agent | decision majoritairement seuil net_score | synthese + arbitrage explicite | debat sous-utilise | inclure score debat et quality gates | P0 |
| risk-manager | deterministic only (enforced) | deterministic only | ecart corrige | conserver lock LLM OFF | P0 |
| execution-manager | deterministic only (enforced) | deterministic only | ecart corrige | conserver lock LLM OFF | P0 |
| schedule-planner-agent | JSON strict + sanitization | conforme | faible | reutiliser ce pattern ailleurs | P1 |
| prompt API | `agent_name` libre | enum agents supportes | drift possible | validation stricte des agents | P1 |

## Tableau: agent_role_clarity

| agent | intended_role | actual_role | overlap_or_conflict | recommended_adjustment | priority |
|---|---|---|---|---|---|
| technical-analyst | signal technique | trend/rsi/macd score | overlap macro/sentiment | enrichir multi-timeframe | P1 |
| news-analyst | impact news | sentiment headlines | pas de score fraicheur fort | scorer impact + recence | P1 |
| macro-analyst | contexte macro | proxy volatilite+tendance | overlap technique | fusionner ou brancher donnees macro reelles | P0 |
| sentiment-agent | sentiment marche | change_pct court terme | overlap technique | fusionner ou source sentiment dediee | P0 |
| bullish-researcher | these haussiere | filtre scores positifs | debat peu discriminant | structurer argumentation | P0 |
| bearish-researcher | these baissiere | filtre scores negatifs | debat peu discriminant | structurer argumentation | P0 |
| trader-agent | arbitrage final | seuil net_score | debat pas vraiment arbitre | politique de decision explicite | P0 |
| risk-manager | controle risque | deterministic strict | ecart resolu | maintenir deterministic-only | P0 |
| execution-manager | traduction ordre | deterministic strict | ecart resolu | maintenir deterministic-only | P0 |
| order-guardian | supervision positions | decide via trader_decision | risk output non exploite | inclure validation risque | P1 |

## Tableau: context_memory_review

| flow | current_context | problem | recommended_context_strategy | expected_benefit |
|---|---|---|---|---|
| run bootstrap | market + news + memory top5 | pas de budget tokens explicite | budget par agent + resume hierarchique | latence/cout plus stables |
| news-analyst | headlines + memory summaries | recence peu exploitee | score impact/age des news | moins de bruit |
| debate researchers | `signals_json` complet des 2 cotes | redondance forte | resume intermediaire partage | baisse tokens |
| trader decision | arguments bull/bear surtout traces | faible impact sur vote | integrer score debat dans l'arbitrage | decision plus robuste |
| long-term memory | embedding hash deterministe | faible semantique | embeddings semantiques + decay | meilleur rappel |

## Tableau: trading_decision_review

| decision_flow | current_logic | risk_or_weakness | recommended_improvement | priority |
|---|---|---|---|---|
| aggregation | somme des scores agents | conflits mal geres | agregation ponderee + calibration | P0 |
| BUY/SELL/HOLD | seuils fixes +/-0.2 | regime shifts non captes | seuils adaptatifs par regime volatilite | P1 |
| SL/TP | ATR multiples fixes | spread/session non pris en compte | regler via specs symbole + contexte execution | P1 |
| position sizing | pip_value fixe | volumes incoherents selon instrument | sizing instrument-aware | P0 |
| execution payload | statuts heterogenes selon mode | ambiguite suivi run | contrat execution unifie | P0 |

## Tableau: integration_test_plan

| test_name | scope | dependencies | expected_result | priority |
|---|---|---|---|---|
| run multi-agent complet forex | API->worker->orchestrator | DB, queue, providers | 9 etapes + decision/risk/execution coherents | P0 |
| chaine technical->debate->trader | raisonnement | prompts + llm mocks/reel | arbitrage coherent des signaux contradictoires | P0 |
| pipeline trader->risk->execution | metier critique | risk engine + execution | blocage fiable des trades fragiles | P0 |
| validation JSON sortie chaque agent | contrat data | schemas agent | sorties conformes ou fallback propre | P0 |
| reponse LLM incomplete/invalide | resilience | mock llm | pas de crash, decision degradee explicite | P0 |
| indisponibilite provider externe | mode degrade | yfinance/metaapi/llm down | run termine avec traces degradees | P0 |
| dashboard->API->resultat final | e2e | frontend/backend/worker | statut UI fidele au backend | P1 |
| run memoire ON vs OFF | qualite contexte | memory service | impact mesure et borne | P1 |

## Tableau: performance_test_plan

| scenario | target_component_or_flow | metric | load_profile | success_criteria | priority |
|---|---|---|---|---|---|
| run multi-agent complet | orchestrator | p50/p95 duree run | 30 runs, 5 concurrents | p95 sous budget + variance stable | P0 |
| latence par agent | workflow etapes | p95 par agent | 50 runs | pas de regressions majeures | P0 |
| contexte long vs reduit | prompts llm | tokens/run, latence | A/B | gain cout sans perte decisionnelle | P1 |
| prompts longs vs optimises | prompt engineering | stabilite decision | A/B | baisse variance inter-runs | P1 |
| llm parallelise vs sequentiel | orchestration | duree totale | 20 runs par mode | gain net latence | P1 |
| cache warm vs cold | yfinance/metaapi/memory | hit ratio + latence | alternance | gain latence en warm | P1 |
| charge concurrente runs | celery/rabbitmq | throughput, backlog | burst 50 runs | backlog non divergent | P0 |
| latence decision->execution | trader->risk->execution | p95 pipeline | 30 runs BUY/SELL | sous SLA metier | P0 |

## Recommandations prioritaires

| Priorite | Recommandation | Statut |
|---|---|---|
| P0 | schema JSON strict + validation forte pour sorties agents | A_FAIRE |
| P0 | revisiter logique trader (debate-aware + quality gates) | TERMINE (lot 1) |
| P0 | sizing risque instrument-aware (plus de pip fixe global) | A_FAIRE |
| P0 | bloquer LLM sur risk/execution en prod + aligner docs/UI | TERMINE (lot 1) |
| P1 | fusionner ou re-specialiser macro/sentiment | A_FAIRE |
| P1 | memoire semantique + filtre recence/pertinence | A_FAIRE |
| P1 | campagne integration/perf automatisee CI | A_FAIRE |
| P2 | optimisation cout latence par resume/caching intermediaire | A_FAIRE |

## Journal d'avancement

| Date | Action | Statut |
|---|---|---|
| 2026-03-17 | Lecture complete architecture/code/prompts/workflows/docs | TERMINE |
| 2026-03-17 | Audit multi-agent prompt/trading + gaps priorises | TERMINE |
| 2026-03-17 | Verification technique (`pytest`, `frontend build`) | TERMINE |
| 2026-03-17 | Formalisation plan integration/performance | TERMINE |
| 2026-03-17 | Creation du present tracker markdown | TERMINE |
| 2026-03-17 | Correctif P0: lock deterministic `risk-manager`/`execution-manager` (selector/API/UI) | TERMINE |
| 2026-03-17 | Correctif P0: arbitrage trader avec `debate_score`/`combined_score` + conflict gate | TERMINE |
| 2026-03-17 | Correctif P0: contrat execution unifie (`status`,`executed`,`reason`) | TERMINE |
| 2026-03-17 | Validation post-correctifs lot 1 (`backend pytest -q` -> `85 passed`, `frontend build` OK) | TERMINE |

## Prochaines etapes recommandees

| Ordre | Etape | Statut |
|---|---|---|
| 1 | Implementer correctifs P0 sur decision/risk/execution contracts | EN_COURS (lot 1 termine, lot 2 restant) |
| 2 | Ajouter tests P0 integration + resilience LLM invalid | A_FAIRE |
| 3 | Lancer benchmark performance de reference (baseline) | A_FAIRE |
| 4 | Traiter P1 (specialisation agents + memoire semantique) | A_FAIRE |
