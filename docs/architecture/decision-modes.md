# Decision Modes — Guide complet

**Date:** 2026-04-01
**Derniere mise a jour:** 2026-04-01
**Fichiers source:** `app/services/agentscope/constants.py`, `app/services/risk/limits.py`, `app/services/config/trading_config.py`

---

## Vue d'ensemble

Le systeme de decision repose sur deux axes independants :

1. **Decision Mode** (conservative / balanced / permissive) — controle **quand** on decide de trader (seuils de declenchement)
2. **Risk Limits** (simulation / paper / live) — controle **combien** on risque quand on trade (contraintes de portefeuille)

```
                        Decision Mode
                        (quand trader ?)
                              |
    Phase 1 (analysts) --> Trader-Agent --> decision_gating() --> BUY / SELL / HOLD
                              |                    |
                         Debate result      min_score, min_confidence,
                                            min_aligned_sources
                              |
                              v
                        Risk Limits
                        (combien risquer ?)
                              |
                    Risk-Manager --> evaluate_portfolio() --> ACCEPT / REJECT
                              |
                    max_daily_loss, max_positions,
                    max_exposure, VaR, stress test
```

---

## Les 3 Decision Modes

### CONSERVATIVE — "Le banquier suisse"

**Philosophie :** Ne trader que quand tout converge. Preferer rater un trade que prendre un mauvais.

| Parametre | Valeur | Effet |
|-----------|--------|-------|
| Min combined score | **0.32** | Signal fort et clair requis |
| Min confidence | **0.38** | Refuse les setups incertains |
| Min sources alignees | **2** | Au moins 2 agents sur 3 doivent etre d'accord |
| Override technique seul | **Non** | Le technique seul ne suffit jamais |
| Block contradiction majeure | **Oui** | Contradiction majeure = trade impossible |
| Penalite contradiction moderee | **-0.08** | Presque fatale pour le signal |
| Penalite contradiction majeure | **-0.14** | Rend le trade impossible |
| Multiplicateur confiance moderee | **x0.80** | Confiance fortement reduite |
| Multiplicateur confiance majeure | **x0.60** | Confiance quasi detruite |

**Profil :**
- Trade rarement (peut-etre 1 fois sur 5-6 runs)
- Quand il trade, c'est avec haute conviction
- Ne prend jamais de position counter-trend
- Exige un consensus multi-source

**En une phrase :** *"Si je dois reflechir, c'est non."*

**Adapte pour :** live trading avec du vrai capital, compte small, trader debutant

---

### BALANCED — "Le trader professionnel" (defaut)

**Philosophie :** Prendre les bons setups quand l'edge est raisonnable, sans etre trop exigeant.

| Parametre | Valeur | Effet |
|-----------|--------|-------|
| Min combined score | **0.22** | Signal modere suffit |
| Min confidence | **0.28** | Accepte un doute raisonnable |
| Min sources alignees | **1** | Un seul agent convaincant suffit |
| Override technique seul | **Oui** | Le technique peut porter la decision seul |
| Block contradiction majeure | **Oui** | Contradiction majeure reste bloquante |
| Penalite contradiction moderee | **-0.06** | Penalise mais ne bloque pas |
| Penalite contradiction majeure | **-0.11** | Gros frein sans etre eliminatoire |
| Multiplicateur confiance moderee | **x0.85** | Reduction moderee |
| Multiplicateur confiance majeure | **x0.70** | Reduite mais pas detruite |

**Profil :**
- Trade quand il voit un edge clair
- Tolere du bruit (1 agent neutre sur 3 n'est pas bloquant)
- Peut trader sur la technique seule si le setup est propre

**En une phrase :** *"L'edge est la, les conditions sont correctes, on y va."*

**Adapte pour :** paper trading, validation de strategie, compte avec budget de risque confortable

---

### PERMISSIVE — "Le scalper agressif"

**Philosophie :** Maximiser le nombre d'opportunites. Chaque signal faible est un trade potentiel.

| Parametre | Valeur | Effet |
|-----------|--------|-------|
| Min combined score | **0.13** | Signal tres faible peut declencher un trade |
| Min confidence | **0.25** | Accepte de trader avec 75% de doute |
| Min sources alignees | **1** | Un seul agent qui penche suffit |
| Override technique seul | **Oui** | Idem balanced |
| Block contradiction majeure | **Oui** | Reste le seul garde-fou absolu |
| Penalite contradiction faible | **-0.01** | Meme les contradictions faibles sont notees |
| Penalite contradiction majeure | **-0.08** | La plus faible — ralentit mais ne bloque pas |
| Multiplicateur confiance moderee | **x0.90** | Presque inchangee |
| Multiplicateur confiance majeure | **x0.75** | Garde plus de confiance que les autres modes |

**Profil :**
- Trade souvent (la majorite des runs produisent un signal)
- Accepte les setups faibles et counter-trend
- Plus de trades = plus de bruit, mais aussi plus de captures d'opportunites

**En une phrase :** *"Il y a peut-etre quelque chose ici, essayons."*

**Adapte pour :** simulation/backtesting, exploration de strategie, marches tres actifs

---

## Comparaison directe

### Seuils de declenchement

| Parametre | Conservative | Balanced | Permissive |
|-----------|:----------:|:-------:|:---------:|
| Min score | 0.32 | 0.22 | 0.13 |
| Min confidence | 0.38 | 0.28 | 0.25 |
| Min sources | 2 | 1 | 1 |
| Override tech seul | Non | Oui | Oui |

### Penalites de contradiction

| Severite | Conservative | Balanced | Permissive |
|----------|:----------:|:-------:|:---------:|
| Faible (weak) | 0.00 | 0.00 | 0.01 |
| Moderee | 0.08 | 0.06 | 0.04 |
| Majeure | 0.14 | 0.11 | 0.08 |

### Multiplicateurs de confiance

| Severite | Conservative | Balanced | Permissive |
|----------|:----------:|:-------:|:---------:|
| Moderee | x0.80 | x0.85 | x0.90 |
| Majeure | x0.60 | x0.70 | x0.75 |

### Profil visuel

```
Frequence de trading :    ####......  Conservative
                          ######....  Balanced
                          #########.  Permissive

Qualite par trade :       #########.  Conservative
                          #######...  Balanced
                          #####.....  Permissive

Tolerance au bruit :      ##........  Conservative
                          #####.....  Balanced
                          ########..  Permissive

Risque de faux signal :   ##........  Conservative
                          ####......  Balanced
                          #######...  Permissive
```

---

## Interaction avec les Risk Limits

Les decision modes et les risk limits sont **deux axes independants** qui se combinent :

| Decision Mode \ Risk Mode | Simulation | Paper | Live |
|---------------------------|:----------:|:-----:|:----:|
| **Permissive** | Beaucoup de trades, gros volumes, pour tester | Beaucoup de trades, volumes moyens | Possible mais dangereux |
| **Balanced** | Trades frequents, exploration | Usage standard | Usage recommande |
| **Conservative** | Peu utile (trop peu de trades) | Validation finale | Le plus sur |

### Risk Limits par mode d'execution

| Parametre | Simulation | Paper | Live |
|-----------|:----------:|:-----:|:----:|
| Max risk par trade | 5% | 3% | 2% |
| Max perte journaliere | 10% | 6% | 3% |
| Max risque ouvert total | 15% | 10% | 6% |
| Max positions | 10 | 5 | 3 |
| Max positions par symbole | 3 | 2 | 1 |
| Min marge libre | 20% | 30% | 50% |
| Max exposition par devise | 40% | 25% | 15% |
| Max exposition brute | 100% | 60% | 40% |
| Max multiplicateur correlation | 3.0 | 2.0 | 1.5 |
| Max perte hebdo | 15% | 10% | 5% |
| Max VaR 95% | 15% | 10% | 5% |
| Max VaR marginal | 5% | 3% | 2% |
| Min diversification | 0.2 | 0.3 | 0.4 |
| Stress tests requis | risk_off | risk_off, flash_crash | risk_off, flash_crash, usd_crash |

---

## Impact des valeurs sur le comportement des modes

Chaque parametre configurable a un impact direct sur la frequence, la qualite et le risque des trades.
Voici comment les ajuster intentionnellement :

### Decision Gating — ce qui change entre les modes

| Quand vous modifiez... | Effet concret |
|------------------------|---------------|
| **min_combined_score** de 0.22 → 0.10 | Le systeme trade beaucoup plus souvent. Des signaux faibles deviennent executables. Utile en simulation pour explorer, dangereux en live (plus de faux signaux). |
| **min_combined_score** de 0.22 → 0.40 | Le systeme trade tres rarement. Seuls les signaux tres forts passent. Bon pour le live, mais risque de rater des opportunites. |
| **min_confidence** de 0.28 → 0.15 | Le systeme accepte des trades meme quand il n'est pas tres sur. Augmente le volume de trades mais aussi le taux d'erreur. |
| **min_confidence** de 0.28 → 0.50 | Le systeme ne trade que quand il est tres confiant. Reduit fortement le nombre de trades. |
| **min_aligned_sources** de 1 → 2 | Exige que 2 agents sur 3 soient d'accord (tech + news, ou tech + context). Filtre les trades ou un seul agent voit un signal. Mode plus conservateur. |
| **min_aligned_sources** de 1 → 0 | Supprime le filtre de consensus. Le systeme peut trader meme si aucun agent n'est convaincu (deconseille). |
| **allow_technical_override** Off → On | Permet a l'analyse technique seule de declencher un trade, meme si news et context sont neutres. Active par defaut en balanced et permissive. |

### Risk Limits — ce qui change entre les modes d'execution

| Quand vous modifiez... | Effet concret |
|------------------------|---------------|
| **max_risk_per_trade** de 2% → 1% | Chaque trade risque moitie moins. Positions plus petites, mais le capital dure plus longtemps. |
| **max_risk_per_trade** de 2% → 5% | Positions plus grosses, gains et pertes amplifies. Adapte a la simulation, dangereux en live. |
| **max_daily_loss** de 3% → 1.5% | Le systeme s'arrete apres 1.5% de perte dans la journee. Protege le capital mais peut couper des series profitables. |
| **max_daily_loss** de 3% → 10% | Permet de perdre beaucoup avant de s'arreter. A n'utiliser qu'en simulation. |
| **max_positions** de 3 → 1 | Une seule position a la fois. Maximum de focus et de controle. Adapte au debut ou en live. |
| **max_positions** de 3 → 10 | Jusqu'a 10 positions simultanees. Diversification maximale mais complexite accrue. |
| **max_positions_per_symbol** de 1 → 3 | Permet de pyramider (ajouter des positions sur le meme instrument). |
| **min_free_margin** de 50% → 20% | Utilise plus de marge, permet des positions plus grosses. Augmente le risque de margin call. |
| **max_currency_exposure** de 15% → 40% | Permet plus de concentration sur une devise. Augmente le risque si cette devise se retourne. |
| **max_weekly_loss** de 5% → 15% | Plus de tolerance aux mauvaises semaines. Adapte a la simulation. |

### Trade Sizing — impact sur le R:R

| Quand vous modifiez... | Effet concret |
|------------------------|---------------|
| **sl_atr_multiplier** de 1.5 → 1.0 | SL plus serre. Le trade est stoppe plus facilement par le bruit, mais la perte par trade est plus petite. |
| **sl_atr_multiplier** de 1.5 → 2.5 | SL plus large. Donne plus de respiration au trade, mais augmente le risque par position. |
| **tp_atr_multiplier** de 2.5 → 4.0 | TP plus ambitieux. Meilleur R:R (4.0/1.5 = 2.67) mais les trades atteignent moins souvent le TP. |
| **tp_atr_multiplier** de 2.5 → 1.5 | TP plus court. R:R = 1.0, chaque trade vise un gain egal au risque. Plus de trades gagnants mais gains plus petits. |

### Combiner les parametres — exemples concrets

**"Je veux trader tres rarement mais avec haute conviction" :**
```
min_combined_score = 0.40
min_confidence = 0.45
min_aligned_sources = 2
max_risk_per_trade = 2%
max_positions = 2
```

**"Je veux explorer un maximum de signaux en simulation" :**
```
min_combined_score = 0.10
min_confidence = 0.20
min_aligned_sources = 0
max_risk_per_trade = 5%
max_positions = 10
```

**"Je veux un R:R agressif avec SL serre" :**
```
sl_atr_multiplier = 1.0
tp_atr_multiplier = 4.0
→ R:R = 4.0 — on gagne 4x ce qu'on risque, mais on se fait stopper plus souvent
```

**"Je veux proteger mon capital au maximum en live" :**
```
max_daily_loss = 1.5%
max_weekly_loss = 3%
max_positions = 1
min_free_margin = 60%
max_currency_exposure = 10%
```

---

## Parcours recommande

La progression naturelle pour un nouveau trader ou une nouvelle strategie :

### Phase 1 : Exploration
```
Decision Mode : permissive
Risk Mode    : simulation
Objectif     : generer un maximum de signaux, backtester, calibrer les parametres
Duree        : jusqu'a obtenir un echantillon significatif (50-100 trades)
```

### Phase 2 : Validation
```
Decision Mode : balanced
Risk Mode    : paper
Objectif     : valider en conditions reelles sans risque de capital
Duree        : 2-4 semaines, verifier que les metriques (win rate, Sharpe) tiennent
```

### Phase 3 : Production
```
Decision Mode : conservative
Risk Mode    : live
Objectif     : trading reel avec protection maximale
Duree        : continu, ajuster si les resultats le justifient
```

---

## Pipeline de decision detaille

### Etape 1 : Score deterministe pre-calcule

Avant l'appel du trader-agent LLM, un `deterministic_combined_score` est calcule :

```
score = tech_score * 0.50 * tech_confidence
      + news_score * 0.25 * news_confidence
      + ctx_score  * 0.25 * ctx_confidence
      (normalise par la somme des poids effectifs)

+ bonus si le debat converge avec le score (+10% * debate_confidence)
- penalite si le debat contredit le score (-5% * debate_confidence)
```

Le LLM peut ajuster ce score de **+/- 20% maximum**. Au-dela, le score est clampe.

**Fichier :** `app/services/agentscope/decision_helpers.py`

### Etape 2 : Decision Gating

Le trader-agent appelle `decision_gating()` avec :
- `combined_score` : son score (contraint dans la bande)
- `confidence` : sa confiance dans la decision
- `aligned_sources` : **pre-injecte** (compte deterministe des agents alignes)

Le tool retourne `gates_passed=true/false` selon la policy du mode.

**Fichier :** `app/services/mcp/trading_server.py:1256-1271`

### Etape 3 : Contradiction Detector

Le trader-agent appelle `contradiction_detector()` avec :
- `macd_diff` : **pre-injecte** depuis le snapshot
- `atr` : **pre-injecte** depuis le snapshot
- `trend` : **pre-injecte** (derive du snapshot)
- `momentum` : **pre-injecte** (derive du signe de macd_diff)

Le LLM ne fournit plus aucune valeur numerique — tout est factuel.

**Fichier :** `app/services/mcp/trading_server.py:1274-1293`

### Etape 4 : Trade Sizing (BUY/SELL uniquement)

Si la decision est BUY ou SELL, le trader-agent appelle `trade_sizing()` avec :
- `price` : **pre-injecte** (last_price du snapshot)
- `atr` : **pre-injecte** (ATR du snapshot)
- `decision_side` : BUY ou SELL (fourni par le LLM)

Calcule entry, stop_loss, take_profit via ATR :
- SL = 1.5x ATR
- TP = 2.5x ATR
- R:R structurel = 1.67

**Fichier :** `app/services/mcp/trading_server.py:1296-1306`

### Etape 5 : Validation post-LLM

Apres l'appel du trader-agent, le pipeline verifie :
1. **Tool calls requis** : `decision_gating` et `contradiction_detector` ont-ils ete appeles ? Si non → `execution_allowed=false` force
2. **Score dans la bande** : `combined_score` est-il dans [band_min, band_max] ? Si non → clampe
3. **Score recovery** : si `combined_score` absent → fallback sur le score deterministe

**Fichier :** `app/services/agentscope/registry.py` (Phase 4, apres trader-agent)

---

## Configuration

### 3 niveaux de configuration

Tous les parametres de decision et de risque suivent la hierarchie :

```
ConnectorConfig DB (runtime, modifiable depuis le frontend)
         |
         v  (si absent)
   Variables d'environnement (.env)
         |
         v  (si absent)
   Valeurs par defaut dans le code (constants.py, limits.py)
```

Les overrides DB sont charges via `RuntimeConnectorSettings` avec un cache de 5 secondes.
Modifier un parametre dans le frontend est instantane (pas besoin de redemarrer).

### Depuis le frontend (recommande)

Page **Config > Trading > TRADING_PARAMETERS** — 3 sections editables :

#### Decision Gating — Seuils de declenchement

| Parametre | Description | Defaut (balanced) |
|-----------|-------------|:-----------------:|
| **Min Combined Score** | Score minimum pour declencher un trade. Plus c'est haut, moins de trades sont pris. | 0.22 |
| **Min Confidence** | Niveau de confiance minimum requis. En dessous, le trade est bloque meme si le score est bon. | 0.28 |
| **Min Aligned Sources** | Nombre minimum d'agents (tech, news, context) qui doivent etre d'accord sur la direction. 1 = un seul agent suffit, 2 = consensus requis. | 1 |
| **Allow Technical Override** | Si actif, l'analyse technique seule peut declencher un trade meme si news et context sont neutres. | Oui |

#### Risk Limits — Contraintes de portefeuille

| Parametre | Description | Defaut (simulation) |
|-----------|-------------|:-------------------:|
| **Max Risk Per Trade (%)** | Risque maximum par trade en pourcentage de l'equity. Ex: 2% = on risque max 200 sur un compte de 10 000. | 5.0% |
| **Max Daily Loss (%)** | Perte maximale autorisee sur une journee. Au-dela, tous les trades sont bloques jusqu'au lendemain. | 10.0% |
| **Max Open Risk (%)** | Risque total maximum de toutes les positions ouvertes combinees. Empeche la surexposition. | 15.0% |
| **Max Positions** | Nombre maximum de positions ouvertes en meme temps. Limite la complexite du portefeuille. | 10 |
| **Max Positions Per Symbol** | Nombre maximum de positions sur le meme instrument. 1 = une seule position par paire. | 3 |
| **Min Free Margin (%)** | Pourcentage minimum de marge libre requis. Protege contre le margin call en gardant une reserve. | 20.0% |
| **Max Currency Exposure (%)** | Exposition maximale sur une seule devise. Evite la concentration (ex: tout en USD). | 40.0% |
| **Max Weekly Loss (%)** | Perte maximale autorisee sur une semaine. Au-dela, les trades sont bloques jusqu'a la semaine suivante. | 15.0% |

#### Trade Sizing — Calcul SL/TP

| Parametre | Description | Defaut |
|-----------|-------------|:------:|
| **SL ATR Multiplier** | Multiplicateur ATR pour le stop loss. Ex: 1.5 = SL place a 1.5x la volatilite moyenne. Plus c'est haut, plus le SL est loin. | 1.5 |
| **TP ATR Multiplier** | Multiplicateur ATR pour le take profit. Ex: 2.5 = TP place a 2.5x la volatilite. Ratio R:R = TP/SL (2.5/1.5 = 1.67). | 2.5 |

### Depuis les variables d'environnement

```env
# .env
DECISION_MODE=balanced    # conservative | balanced | permissive
```

Le decision mode dans `.env` selectionne le jeu de valeurs par defaut.
Les overrides individuels dans le frontend s'appliquent **par-dessus** ces defaults.

### Architecture technique

```
Frontend (ConnectorsPage.tsx)
    |
    |  PUT /connectors/trading  { settings: { gating: {...}, risk_limits: {...}, sizing: {...} } }
    |
    v
ConnectorConfig DB (table connector_configs, connector_name='trading')
    |
    |  RuntimeConnectorSettings.settings('trading')  (cache 5s)
    |
    v
trading_config.py
    |
    |--- get_effective_gating_policy(mode)  → DecisionGatingPolicy avec overrides
    |--- get_effective_risk_limits(mode)    → RiskLimits avec overrides
    |--- get_effective_sizing()             → ATR multipliers avec overrides
    |
    v
Consumers:
    - decision_gating() tool        → utilise get_effective_gating_policy()
    - get_risk_limits()             → utilise get_effective_risk_limits()
    - trade_sizing() tool           → utilise get_effective_sizing()
    - evaluate_portfolio()          → via get_risk_limits()
    - ExecutionPreflightEngine      → via get_risk_limits()
```

**Endpoint API :** `GET /connectors/trading-config?decision_mode=balanced&execution_mode=simulation`
Retourne le catalogue de parametres (avec descriptions) + les valeurs effectives courantes.

### Fichiers de reference

| Fichier | Contenu |
|---------|---------|
| `app/services/config/trading_config.py` | Catalogue de parametres, resolution DB > env > defaults, descriptions |
| `app/services/agentscope/constants.py` | DecisionGatingPolicy, valeurs par defaut par mode |
| `app/services/risk/limits.py` | RiskLimits, valeurs par defaut par mode d'execution |
| `app/services/agentscope/decision_helpers.py` | Score deterministe, aligned sources, trend/momentum |
| `app/services/mcp/trading_server.py` | Tools : decision_gating, contradiction_detector, trade_sizing |
| `app/services/agentscope/prompts.py` | Prompt trader-agent avec sequence obligatoire |
| `app/core/config.py` | DECISION_MODE, EXECUTION_MANAGER_LLM_ENABLED |
| `app/api/routes/connectors.py` | Endpoint GET /connectors/trading-config |
| `frontend/src/pages/ConnectorsPage.tsx` | UI section TRADING_PARAMETERS |
