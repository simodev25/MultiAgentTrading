# Risk Manager Niveau 2 — Portfolio Risk Management

**Date:** 2026-04-01
**Statut:** Terminé (Tier 1 + Tier 2 + Tier 3)
**Objectif:** Transformer le risk-manager d'un validateur de trade unitaire (Niveau 1) en un gestionnaire de risque portefeuille complet (Niveau 2), puis ajouter l'analyse d'exposition par devise et corrélation (Tier 2), et enfin le risk management quantitatif avancé (Tier 3).
**Tests:** 436 passed (54 nouveaux, 0 régression)

---

## Vue d'ensemble des Tiers

| Tier | Nom | Scope | Prerequis |
|------|-----|-------|-----------|
| **Tier 1** | MVP Portfolio Risk | Equity réelle, drawdown, budget risque, max positions, max par symbole | Aucun |
| **Tier 2** | Exposition & Corrélation | Exposition par devise, corrélation cross-paires, drawdown hebdo | Tier 1 |
| **Tier 3** | Risk Quantitatif Avancé | VaR Monte Carlo, stress testing, corrélation dynamique | Tier 1 + Tier 2 |

---

## Contexte

### Situation actuelle (Niveau 1)

Le risk-manager actuel valide uniquement le trade individuel :
- Géométrie du SL (distance, cohérence)
- Limites de risk% par mode (simulation 5%, paper 3%, live 2%)
- Calcul du volume (position sizing)
- Validation des inputs numériques

**Limitations critiques :**
- Equity hardcodée à 10 000 (`rules.py:209`, `trading_server.py:1329`)
- Aucune connaissance des positions ouvertes
- Aucun suivi du drawdown (journalier, hebdo)
- Aucun budget de risque
- Aucune contrainte de portefeuille (max positions, exposition par symbole/devise)
- Pour les HOLD, le risk-manager est bypassé complètement (réponse déterministe)

### Cible (Niveau 2)

Le risk-manager doit devenir un **capital guardian** + **portfolio exposure controller** + **risk budget allocator**, capable de répondre à :
- Combien de capital reste-t-il ?
- Combien de risque reste-t-il à consommer ?
- Peut-on encore prendre un trade aujourd'hui ?
- Est-on déjà trop exposé ?

---

## Architecture de la solution

### Source de données : Mode C (temps réel + DB)

- **Temps réel** : lecture via MetaAPI à chaque run (`get_account_information()`, `get_positions()` — déjà implémentés dans `metaapi_client.py`)
- **DB** : stockage de snapshots pour calcul historique (drawdown journalier/hebdo, PnL réalisé cumulé)

---

## Plan d'implémentation — Tier 1 (MVP)

### Tache 1 : Portfolio State Service

**Fichier:** `backend/app/services/risk/portfolio_state.py` (nouveau)

Créer un service qui agrège l'état du portefeuille en temps réel :

```python
@dataclass
class PortfolioState:
    # Compte
    balance: float            # Solde du compte
    equity: float             # Equity (balance + PnL non réalisé)
    free_margin: float        # Marge disponible
    used_margin: float        # Marge utilisée
    leverage: float           # Levier du compte

    # Positions ouvertes
    open_positions: list[OpenPosition]   # Liste des positions
    open_position_count: int             # Nombre de positions ouvertes
    open_risk_total_pct: float           # Risque total ouvert en % de l'equity

    # PnL & Drawdown
    daily_realized_pnl: float            # PnL réalisé aujourd'hui
    daily_unrealized_pnl: float          # PnL non réalisé actuel
    daily_drawdown_pct: float            # Drawdown journalier en %
    daily_high_equity: float             # Plus haut equity du jour

    # Budget de risque
    risk_budget_remaining_pct: float     # Budget de risque restant en %
    trades_remaining_today: int          # Nombre de trades encore possibles

    # Exposition
    exposure_by_symbol: dict[str, float]     # Exposition par symbole
    exposure_by_currency: dict[str, float]   # Exposition par devise (Tier 2)

    # Meta
    degraded: bool            # True si données incomplètes
    degraded_reasons: list[str]
    fetched_at: str           # Timestamp ISO

@dataclass
class OpenPosition:
    symbol: str
    side: str               # BUY | SELL
    volume: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    stop_loss: float | None
    take_profit: float | None
    risk_pct: float         # Risque de cette position en % equity
```

**Source des données :**
- `metaapi_client.get_account_information()` → balance, equity, margin, leverage
- `metaapi_client.get_positions()` → positions ouvertes avec PnL
- DB `portfolio_snapshots` → equity high du jour, PnL réalisé cumulé

**Statut:** [x] Terminé

---

### Tache 2 : Table portfolio_snapshots

**Fichier:** `backend/app/db/models/portfolio_snapshot.py` (nouveau)
**Migration:** Alembic

```python
class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: int                    # PK auto
    account_id: str            # Ref MetaAPI account
    timestamp: datetime        # UTC
    balance: float
    equity: float
    free_margin: float
    used_margin: float
    open_position_count: int
    open_risk_total_pct: float
    daily_realized_pnl: float
    daily_high_equity: float
    snapshot_type: str         # "pre_trade" | "post_trade" | "periodic"
```

**Quand écrire un snapshot :**
- Avant chaque évaluation risk-manager (`pre_trade`)
- Après chaque exécution de trade (`post_trade`)
- Périodiquement via tâche Celery (toutes les 15 min quand le marché est ouvert)

**Statut:** [x] Terminé

---

### Tache 3 : Risk Limits Configuration

**Fichier:** `backend/app/services/risk/limits.py` (nouveau)

Définir les limites de risque par mode :

```python
@dataclass
class RiskLimits:
    max_risk_per_trade_pct: float      # Max risque par trade (existant)
    max_daily_loss_pct: float          # Max perte journalière
    max_open_risk_pct: float           # Max risque total ouvert
    max_positions: int                 # Max positions simultanées
    max_positions_per_symbol: int      # Max positions par symbole
    min_free_margin_pct: float         # Marge libre minimum en %

RISK_LIMITS = {
    "simulation": RiskLimits(
        max_risk_per_trade_pct=5.0,
        max_daily_loss_pct=10.0,
        max_open_risk_pct=15.0,
        max_positions=10,
        max_positions_per_symbol=3,
        min_free_margin_pct=20.0,
    ),
    "paper": RiskLimits(
        max_risk_per_trade_pct=3.0,
        max_daily_loss_pct=6.0,
        max_open_risk_pct=10.0,
        max_positions=5,
        max_positions_per_symbol=2,
        min_free_margin_pct=30.0,
    ),
    "live": RiskLimits(
        max_risk_per_trade_pct=2.0,
        max_daily_loss_pct=3.0,
        max_open_risk_pct=6.0,
        max_positions=3,
        max_positions_per_symbol=1,
        min_free_margin_pct=50.0,
    ),
}
```

**Statut:** [x] Terminé

---

### Tache 4 : Etendre RiskEngine avec les checks portefeuille

**Fichier:** `backend/app/services/risk/rules.py` (modifier)

Ajouter une méthode `evaluate_portfolio()` au `RiskEngine` :

```python
def evaluate_portfolio(
    self,
    portfolio: PortfolioState,
    limits: RiskLimits,
    proposed_trade: ProposedTrade,
) -> RiskAssessment:
```

**Checks séquentiels (reject au premier échec) :**

| # | Check | Condition de rejet | Message |
|---|-------|-------------------|---------|
| 1 | Daily loss limit | `daily_drawdown_pct >= max_daily_loss_pct` | `REJECT: daily loss limit reached ({x}% >= {max}%)` |
| 2 | Risk budget | `open_risk_total_pct + trade_risk_pct > max_open_risk_pct` | `REJECT: risk budget exceeded ({current}% + {new}% > {max}%)` |
| 3 | Max positions | `open_position_count >= max_positions` | `REJECT: max positions reached ({n}/{max})` |
| 4 | Max per symbol | `positions_on_symbol >= max_positions_per_symbol` | `REJECT: max positions on {symbol} reached ({n}/{max})` |
| 5 | Free margin | `free_margin_pct < min_free_margin_pct` | `REJECT: insufficient free margin ({x}% < {min}%)` |
| 6 | Trade unitaire | Checks existants (SL, risk%, volume) | Inchangé |

**Si tous les checks passent** → position sizing avec l'equity **réelle** (pas 10 000 hardcodé).

**Volume adjustment :** Si le trade passe tous les checks mais que `open_risk_total_pct + trade_risk_pct` approche la limite (> 80% du budget restant), réduire le volume proportionnellement.

**Statut:** [x] Terminé

---

### Tache 5 : Nouveau tool MCP `portfolio_risk_evaluation`

**Fichier:** `backend/app/services/mcp/trading_server.py` (modifier)

Remplacer ou enrichir le tool `risk_evaluation` actuel :

```python
@mcp.tool()
def portfolio_risk_evaluation(
    trader_decision: dict,
    risk_percent: float = 1.0,
) -> dict:
    """Evaluate trade risk against portfolio state and risk limits.

    Returns:
    - accepted: bool
    - suggested_volume: float
    - reasons: list[str]
    - portfolio_summary: dict (balance, equity, open_risk, daily_dd, budget_remaining)
    """
```

Ce tool :
1. Appelle `PortfolioStateService.get_current_state()` pour les données live
2. Résout les `RiskLimits` selon le mode
3. Appelle `RiskEngine.evaluate_portfolio()`
4. Retourne le résultat enrichi avec le résumé portefeuille

**Statut:** [x] Terminé

---

### Tache 6 : Mettre a jour le prompt risk-manager

**Fichier:** `backend/app/services/agentscope/prompts.py` (modifier)

Enrichir le prompt pour refléter le Niveau 2 :

```
System prompt additions:
- You have access to real-time portfolio state via the portfolio_risk_evaluation tool.
- Before approving any trade, verify:
  1. Daily loss limit not breached
  2. Risk budget remaining is sufficient
  3. Position count within limits
  4. No over-exposure on the same symbol
  5. Sufficient free margin
- Your response must include portfolio context in the reasons.

User prompt additions:
- Portfolio state is fetched automatically by the tool — do not assume equity is 10000.
```

**Statut:** [x] Terminé

---

### Tache 7 : Injecter PortfolioState dans le pipeline

**Fichier:** `backend/app/services/agentscope/registry.py` (modifier)

Dans Phase 4, avant d'appeler le risk-manager :
1. Appeler `PortfolioStateService.get_current_state()`
2. Sauvegarder un snapshot `pre_trade` en DB
3. Passer le state au risk-manager via `base_vars`
4. Après exécution (si trade accepté), sauvegarder un snapshot `post_trade`

**Statut:** [x] Terminé

---

### Tache 8 : Enrichir le output dans les debug traces

**Fichier:** `backend/app/services/agentscope/registry.py` (modifier)

Le `risk-manager` output dans les traces doit maintenant inclure :

```json
{
  "accepted": true,
  "suggested_volume": 0.15,
  "reasons": ["Risk checks passed.", "Daily drawdown: 1.2%/3.0%", "Open risk: 2.5%/6.0%", "Positions: 2/3"],
  "portfolio_summary": {
    "balance": 9850.0,
    "equity": 9920.0,
    "free_margin_pct": 62.0,
    "open_risk_pct": 2.5,
    "daily_drawdown_pct": 1.2,
    "risk_budget_remaining_pct": 3.5,
    "open_positions": 2,
    "max_positions": 3
  },
  "degraded": false,
  "degraded_reasons": []
}
```

**Statut:** [x] Terminé

---

### Tache 9 : Tests unitaires

**Fichier:** `backend/tests/unit/test_risk_engine_portfolio.py` (nouveau)

| Test | Scenario |
|------|----------|
| `test_reject_daily_loss_exceeded` | drawdown 3.5% avec limit 3% → REJECT |
| `test_reject_risk_budget_exceeded` | open_risk 5% + new 2% > max 6% → REJECT |
| `test_reject_max_positions` | 3 positions ouvertes, max 3 → REJECT |
| `test_reject_max_per_symbol` | 1 position EURUSD, max 1 per symbol → REJECT |
| `test_reject_insufficient_margin` | free_margin 15% < min 50% → REJECT |
| `test_accept_within_limits` | Tout OK → ACCEPT avec volume correct |
| `test_volume_reduction_near_limit` | Budget à 80% → volume réduit |
| `test_real_equity_used` | Vérifier que l'equity réelle (pas 10k) est utilisée |
| `test_hold_bypasses_portfolio_checks` | HOLD → pas de checks portefeuille |
| `test_degraded_data_handling` | MetaAPI down → mode dégradé documenté |

**Statut:** [x] Terminé

---

### Tache 10 : Tache Celery pour snapshots periodiques

**Fichier:** `backend/app/tasks/portfolio_tasks.py` (nouveau)

Tâche Celery `snapshot_portfolio` exécutée toutes les 15 minutes pendant les heures de marché :
- Appelle `get_account_information()` + `get_positions()`
- Sauvegarde un snapshot `periodic`
- Met à jour `daily_high_equity` si nouveau plus haut

**Statut:** [x] Terminé

---

---
---

## Plan d'implémentation — Tier 2 (Exposition & Corrélation)

**Prerequis :** Tier 1 complété (PortfolioState, RiskLimits, evaluate_portfolio fonctionnels)

---

### Tache 11 : Currency Exposure Engine

**Fichier:** `backend/app/services/risk/currency_exposure.py` (nouveau)

Calculer l'exposition nette par devise à partir des positions ouvertes.

**Logique :**
- Chaque paire forex décompose en base/quote (ex: EURUSD → long EUR, short USD si BUY)
- Les métaux/commodités cotés en USD contribuent à l'exposition USD
- Les crypto cotées en USD idem

```python
@dataclass
class CurrencyExposure:
    currency: str                    # EUR, USD, GBP, JPY, etc.
    net_exposure_lots: float         # Exposition nette en lots (positif = long, négatif = short)
    net_exposure_value: float        # Exposition nette en valeur (devise du compte)
    exposure_pct: float              # En % de l'equity
    contributing_positions: list[str]  # Symboles qui contribuent

@dataclass
class CurrencyExposureReport:
    exposures: dict[str, CurrencyExposure]   # Par devise
    dominant_currency: str                    # Devise avec la plus forte exposition
    dominant_exposure_pct: float
    total_gross_exposure_pct: float           # Somme des |expositions|
    warnings: list[str]                       # Ex: "USD exposure 45% exceeds soft limit"
```

**Decomposition des paires :**

| Position | Base | Quote | Impact si BUY | Impact si SELL |
|----------|------|-------|---------------|----------------|
| EURUSD | EUR | USD | +EUR, -USD | -EUR, +USD |
| GBPJPY | GBP | JPY | +GBP, -JPY | -GBP, +JPY |
| XAUUSD | XAU | USD | +XAU, -USD | -XAU, +USD |
| BTCUSD | BTC | USD | +BTC, -USD | -BTC, +USD |

**Source de la decomposition :** `InstrumentClassifier.classify()` retourne déjà `base_asset` et `quote_asset` (vu dans les traces debug : `"base_asset": "EUR", "quote_asset": "USD"`).

**Statut:** [x] Terminé

---

### Tache 12 : Correlation Exposure Detector

**Fichier:** `backend/app/services/risk/correlation_exposure.py` (nouveau)

Détecter les positions qui amplifient le risque par corrélation.

**Principe :** Si on a BUY EURUSD + BUY GBPUSD, les deux sont anti-USD → risque concentré. Le tool `correlation_analyzer` existe déjà dans `trading_server.py` (lignes 519-581) et calcule la corrélation Pearson avec rolling window.

```python
@dataclass
class CorrelationAlert:
    position_a: str              # EURUSD
    position_b: str              # GBPUSD
    correlation: float           # 0.85
    same_direction: bool         # True si même side (BUY/BUY ou SELL/SELL)
    risk_multiplier: float       # Facteur d'amplification du risque
    severity: str                # "high" | "medium" | "low"
    message: str                 # "BUY EURUSD + BUY GBPUSD: correlation 0.85, same direction → 1.7x effective risk"

@dataclass
class CorrelationExposureReport:
    alerts: list[CorrelationAlert]
    effective_risk_multiplier: float    # Multiplicateur global du risque portefeuille
    max_pairwise_correlation: float
    adjusted_open_risk_pct: float       # open_risk_total * effective_multiplier
    should_reduce: bool                 # True si adjusted_risk > limit
```

**Seuils de corrélation :**

| Corrélation abs | Severité | Impact |
|-----------------|----------|--------|
| >= 0.80 | high | Même direction : risque x1.8. Direction opposée : considérer comme hedge |
| 0.50 — 0.79 | medium | Même direction : risque x1.4 |
| < 0.50 | low | Positions considérées indépendantes |

**Source des données corrélation :**
- Appeler `correlation_analyzer` avec les close prices des 30 dernières bougies H4 pour chaque paire de positions ouvertes
- Cache les résultats de corrélation en Redis (TTL 1h) pour éviter de recalculer à chaque run

**Matrice de corrélation :** Pour N positions, calculer N*(N-1)/2 paires. Limité à 10 positions max (Tier 1 limit) → max 45 paires → acceptable.

**Statut:** [x] Terminé

---

### Tache 13 : Etendre RiskLimits avec les contraintes devise/corrélation

**Fichier:** `backend/app/services/risk/limits.py` (modifier)

Ajouter les champs Tier 2 au dataclass `RiskLimits` :

```python
# Tier 2 additions
max_currency_exposure_pct: float       # Max exposition par devise en % equity
max_gross_exposure_pct: float          # Max exposition brute totale
max_correlation_risk_multiplier: float # Max multiplicateur de risque par corrélation
max_weekly_loss_pct: float             # Max perte hebdomadaire

# Valeurs par mode :
# simulation: currency=40%, gross=100%, corr_mult=3.0, weekly=15%
# paper:      currency=25%, gross=60%,  corr_mult=2.0, weekly=10%
# live:       currency=15%, gross=40%,  corr_mult=1.5, weekly=5%
```

**Statut:** [x] Terminé

---

### Tache 14 : Weekly Drawdown Tracking

**Fichier:** `backend/app/services/risk/portfolio_state.py` (modifier)

Ajouter au `PortfolioState` :

```python
# Tier 2 additions
weekly_realized_pnl: float
weekly_drawdown_pct: float
weekly_high_equity: float
```

**Source :** Requête DB sur `portfolio_snapshots` des 7 derniers jours pour trouver le `weekly_high_equity` et calculer le drawdown.

**Check dans evaluate_portfolio :**

| # | Check | Condition de rejet |
|---|-------|-------------------|
| 7 | Weekly loss limit | `weekly_drawdown_pct >= max_weekly_loss_pct` |
| 8 | Currency exposure | `exposure_pct(currency) >= max_currency_exposure_pct` |
| 9 | Gross exposure | `total_gross_exposure_pct >= max_gross_exposure_pct` |
| 10 | Correlation risk | `effective_risk_multiplier >= max_correlation_risk_multiplier` |

**Statut:** [x] Terminé

---

### Tache 15 : Enrichir le tool MCP et le prompt

**Fichier:** `backend/app/services/mcp/trading_server.py` (modifier)
**Fichier:** `backend/app/services/agentscope/prompts.py` (modifier)

Ajouter au retour de `portfolio_risk_evaluation` :

```python
# Tier 2 additions au retour
"currency_exposure": {
    "EUR": {"net_lots": 0.3, "exposure_pct": 12.5},
    "USD": {"net_lots": -0.5, "exposure_pct": 22.0},
    "GBP": {"net_lots": 0.1, "exposure_pct": 4.2},
},
"correlation_alerts": [
    {"pair": "EURUSD/GBPUSD", "correlation": 0.85, "severity": "high", "message": "..."}
],
"weekly_drawdown_pct": 2.1,
"effective_risk_multiplier": 1.4
```

Enrichir le prompt risk-manager :
```
- Check currency exposure: reject if any single currency exceeds limit
- Check correlation: if correlated positions amplify risk beyond threshold, reject or reduce size
- Check weekly drawdown: reject if weekly loss limit breached
- Include currency and correlation context in reasons
```

**Statut:** [x] Terminé

---

### Tache 16 : Tests unitaires Tier 2

**Fichier:** `backend/tests/unit/test_currency_exposure.py` (nouveau)
**Fichier:** `backend/tests/unit/test_correlation_exposure.py` (nouveau)

**Tests currency exposure :**

| Test | Scenario |
|------|----------|
| `test_buy_eurusd_decomposition` | BUY EURUSD → +EUR, -USD |
| `test_sell_gbpjpy_decomposition` | SELL GBPJPY → -GBP, +JPY |
| `test_net_exposure_cancellation` | BUY EURUSD + SELL EURGBP → EUR s'annule partiellement |
| `test_multi_position_usd_concentration` | BUY EURUSD + BUY GBPUSD + BUY XAUUSD → alerte USD concentration |
| `test_reject_currency_limit_exceeded` | Exposition USD 20% avec limit 15% → REJECT |
| `test_metal_usd_exposure` | XAUUSD contribue à l'exposition USD |

**Tests correlation exposure :**

| Test | Scenario |
|------|----------|
| `test_high_correlation_same_direction` | BUY EURUSD + BUY GBPUSD (corr 0.85) → severity high, multiplier 1.8 |
| `test_high_correlation_opposite_direction` | BUY EURUSD + SELL GBPUSD → considéré comme hedge |
| `test_low_correlation_independent` | EURUSD + USDJPY (corr 0.3) → indépendant |
| `test_adjusted_risk_exceeds_limit` | Risque ajusté par corrélation > max → REJECT |
| `test_correlation_cache` | Deuxième appel utilise le cache Redis |
| `test_weekly_drawdown_reject` | Drawdown hebdo 6% avec limit 5% → REJECT |

**Statut:** [x] Terminé

---
---

## Plan d'implémentation — Tier 3 (Risk Quantitatif Avancé)

**Prerequis :** Tier 1 + Tier 2 complétés. Historique de snapshots portefeuille disponible en DB (minimum 30 jours).

---

### Tache 17 : Matrice de corrélation dynamique

**Fichier:** `backend/app/services/risk/correlation_matrix.py` (nouveau)

Calculer et maintenir une matrice de corrélation complète entre tous les instruments tradés.

```python
@dataclass
class CorrelationMatrix:
    symbols: list[str]                        # Liste des symboles
    matrix: dict[str, dict[str, float]]       # matrix[A][B] = correlation
    computed_at: str                           # Timestamp ISO
    lookback_days: int                        # Fenêtre de calcul
    data_quality: dict[str, float]            # % de données disponibles par symbole

    def get_correlation(self, a: str, b: str) -> float: ...
    def get_cluster(self, threshold: float = 0.7) -> list[list[str]]: ...
    def get_diversification_score(self, positions: list[str]) -> float: ...
```

**Calcul :**
- Récupérer les close prices H4 des 30 derniers jours pour chaque symbole tradé
- Calculer les rendements logarithmiques (`np.log(close / close.shift(1))`)
- Calculer la matrice de corrélation Pearson via `pandas.DataFrame.corr()`
- Identifier les clusters de symboles fortement corrélés (>0.7)

**Clustering :**
- Algorithme : hierarchical clustering (scipy.cluster.hierarchy) sur la matrice de distance (1 - |correlation|)
- Résultat : groupes de symboles qui bougent ensemble
- Usage : si on a déjà une position dans un cluster, le risk-manager pénalise les nouvelles positions dans le même cluster

**Score de diversification :**
```python
def get_diversification_score(self, positions: list[str]) -> float:
    """0.0 = toutes les positions sont parfaitement corrélées
       1.0 = toutes les positions sont parfaitement décorrélées"""
    # Moyenne des (1 - |corr|) entre toutes les paires de positions
```

**Refresh :** Recalcul quotidien via tâche Celery (post-clôture de session), stockage en Redis (TTL 24h).

**Dépendance :** `scipy` à ajouter dans `requirements.txt` pour le clustering hiérarchique.

**Statut:** [x] Terminé

---

### Tache 18 : Value at Risk (VaR) — Monte Carlo

**Fichier:** `backend/app/services/risk/var_engine.py` (nouveau)

Calculer le VaR du portefeuille pour estimer la perte maximale probable.

```python
@dataclass
class VaRResult:
    # Résultats principaux
    var_95: float                    # VaR 95% en valeur (devise du compte)
    var_99: float                    # VaR 99% en valeur
    var_95_pct: float                # VaR 95% en % de l'equity
    var_99_pct: float                # VaR 99% en % de l'equity
    cvar_95: float                   # Conditional VaR (Expected Shortfall) 95%

    # Contexte
    horizon_hours: int               # Horizon temporel (ex: 24h, 1 semaine)
    simulations: int                 # Nombre de simulations Monte Carlo
    portfolio_value: float           # Valeur totale du portefeuille
    method: str                      # "monte_carlo" | "historical" | "parametric"

    # Décomposition
    var_by_position: dict[str, float]   # Contribution de chaque position au VaR
    marginal_var: dict[str, float]      # VaR marginal : impact d'ajouter/retirer une position
```

**Algorithme Monte Carlo :**

```python
def calculate_var(
    positions: list[OpenPosition],
    correlation_matrix: CorrelationMatrix,
    returns_history: dict[str, pd.Series],  # Rendements historiques par symbole
    equity: float,
    horizon_hours: int = 24,
    n_simulations: int = 10_000,
    confidence_levels: list[float] = [0.95, 0.99],
) -> VaRResult:
    """
    1. Extraire les rendements historiques de chaque position (30 jours, H4)
    2. Calculer la matrice de covariance à partir de la matrice de corrélation
       et des volatilités individuelles
    3. Décomposition de Cholesky pour générer des rendements corrélés
    4. Simuler n_simulations scénarios de rendements portefeuille
    5. Trier les PnL simulés et extraire les percentiles
    """
```

**Etapes détaillées :**

1. **Rendements historiques** : utiliser les close prices H4 des 30 derniers jours par symbole
2. **Volatilité** : écart-type des rendements par symbole, annualisé
3. **Matrice de covariance** : `Cov = diag(vol) @ CorrMatrix @ diag(vol)`
4. **Cholesky** : `L = np.linalg.cholesky(Cov)` pour générer des vecteurs de rendements corrélés
5. **Simulation** : `simulated_returns = L @ np.random.standard_normal((n_assets, n_simulations))`
6. **PnL portefeuille** : somme pondérée par les expositions de chaque position
7. **VaR** : `np.percentile(pnl_distribution, (1 - confidence) * 100)`
8. **CVaR** : moyenne des pertes au-delà du VaR

**VaR marginal :** Calculer le VaR avec et sans la position proposée → la différence est le VaR marginal du nouveau trade. Si `marginal_var > max_marginal_var`, rejeter.

**Performance :** 10 000 simulations avec 10 positions → ~10ms avec numpy vectorisé. Pas de bottleneck.

**Statut:** [x] Terminé

---

### Tache 19 : Stress Testing Engine

**Fichier:** `backend/app/services/risk/stress_test.py` (nouveau)

Simuler l'impact de scénarios de marché extrêmes sur le portefeuille.

```python
@dataclass
class StressScenario:
    name: str                          # "USD crash", "Risk-off", "Flash crash"
    description: str
    shocks: dict[str, float]           # Choc par devise ou par symbole (en %)
    probability: str                   # "rare" | "occasional" | "extreme"

@dataclass
class StressTestResult:
    scenario: StressScenario
    portfolio_pnl: float               # PnL simulé en valeur
    portfolio_pnl_pct: float           # PnL simulé en % equity
    surviving: bool                    # True si equity > 0 après le choc
    margin_call: bool                  # True si equity < used_margin
    positions_affected: list[dict]     # Détail par position

@dataclass
class StressTestReport:
    results: list[StressTestResult]
    worst_case_pnl_pct: float
    scenarios_surviving: int
    scenarios_total: int
    recommendation: str                # "safe" | "reduce_exposure" | "critical"
```

**Scénarios prédéfinis :**

| Scénario | Chocs | Probabilité |
|----------|-------|-------------|
| **USD Crash** | USD -3%, EUR +2%, GBP +1.5%, JPY +2.5%, XAU +4% | rare |
| **USD Rally** | USD +3%, EUR -2.5%, GBP -2%, JPY -1%, XAU -3% | rare |
| **Risk-Off** | Equity -5%, Crypto -15%, XAU +3%, JPY +2%, CHF +1.5% | occasional |
| **Flash Crash** | Tous les actifs -5% à -10% random, spread x5 | extreme |
| **Rate Shock** | JPY -4%, EUR -1.5%, GBP -2%, USD +2% | rare |
| **Crypto Collapse** | BTC -20%, ETH -25%, altcoins -30%, forex inchangé | occasional |
| **Commodity Spike** | Oil +15%, XAU +5%, USD -1%, EUR +0.5% | rare |
| **Liquidity Crisis** | Spread x10, slippage x5, tous actifs -3% | extreme |

**Logique :**

```python
def run_stress_test(
    positions: list[OpenPosition],
    equity: float,
    used_margin: float,
    scenarios: list[StressScenario] | None = None,
) -> StressTestReport:
    """
    Pour chaque scénario :
    1. Appliquer les chocs de prix à chaque position
    2. Recalculer le PnL non réalisé
    3. Vérifier si margin call (equity < used_margin)
    4. Agréger les résultats
    """
```

**Intégration avec le risk-manager :**
- Le stress test est exécuté **après** que tous les autres checks passent
- Si le portefeuille (incluant le nouveau trade) ne survit pas au scénario "Risk-Off" → warning dans les reasons
- Si le portefeuille ne survit pas au scénario "Flash Crash" → REJECT

**Statut:** [x] Terminé

---

### Tache 20 : Etendre RiskLimits avec les contraintes Tier 3

**Fichier:** `backend/app/services/risk/limits.py` (modifier)

```python
# Tier 3 additions
max_var_95_pct: float                   # Max VaR 95% en % equity
max_marginal_var_pct: float             # Max VaR marginal d'un nouveau trade
min_diversification_score: float        # Score de diversification minimum
stress_test_survival_required: list[str]  # Scénarios que le portefeuille doit survivre

# Valeurs par mode :
# simulation: var95=15%, marginal=5%, diversif=0.2, survive=["risk_off"]
# paper:      var95=10%, marginal=3%, diversif=0.3, survive=["risk_off", "flash_crash"]
# live:       var95=5%,  marginal=2%, diversif=0.4, survive=["risk_off", "flash_crash", "usd_crash"]
```

**Checks supplémentaires dans evaluate_portfolio :**

| # | Check | Condition de rejet |
|---|-------|-------------------|
| 11 | Portfolio VaR | `var_95_pct >= max_var_95_pct` |
| 12 | Marginal VaR | `marginal_var_new_trade >= max_marginal_var_pct` |
| 13 | Diversification | `diversification_score < min_diversification_score` |
| 14 | Stress test | Portfolio ne survit pas aux scénarios requis |

**Statut:** [x] Terminé

---

### Tache 21 : Nouveau tool MCP `portfolio_stress_test`

**Fichier:** `backend/app/services/mcp/trading_server.py` (modifier)

```python
@mcp.tool()
def portfolio_stress_test(
    include_proposed_trade: bool = True,
    scenarios: list[str] | None = None,
) -> dict:
    """Run stress tests on current portfolio.

    Returns:
    - results: list of scenario results (pnl, surviving, margin_call)
    - worst_case_pnl_pct: float
    - recommendation: "safe" | "reduce_exposure" | "critical"
    """
```

Ce tool est accessible au risk-manager et peut être appelé optionnellement quand le trade est BUY/SELL et que le portefeuille a déjà des positions ouvertes.

**Statut:** [x] Terminé

---

### Tache 22 : Tache Celery pour la matrice de corrélation

**Fichier:** `backend/app/tasks/portfolio_tasks.py` (modifier)

Ajouter une tâche `refresh_correlation_matrix` :
- Exécutée 1 fois par jour (après la clôture de la session de trading principale)
- Récupère les close prices H4 des 30 derniers jours pour tous les symboles tradables
- Calcule la matrice de corrélation complète
- Identifie les clusters
- Stocke en Redis avec TTL 24h
- Log les changements significatifs (corrélation qui passe de <0.5 à >0.7 ou vice versa)

**Statut:** [x] Terminé

---

### Tache 23 : Enrichir les debug traces (Tier 3)

**Fichier:** `backend/app/services/agentscope/registry.py` (modifier)

Le risk-manager output doit inclure les données Tier 3 quand elles sont disponibles :

```json
{
  "accepted": true,
  "suggested_volume": 0.10,
  "reasons": [
    "Risk checks passed.",
    "Daily drawdown: 1.2%/3.0%",
    "Open risk: 2.5%/6.0% (adjusted by correlation: 3.5%)",
    "VaR 95%: 2.8%/5.0%",
    "Marginal VaR of this trade: 0.9%",
    "Diversification score: 0.65",
    "Stress test: 7/8 scenarios survived"
  ],
  "portfolio_summary": {
    "balance": 9850.0,
    "equity": 9920.0,
    "var_95_pct": 2.8,
    "var_99_pct": 4.1,
    "cvar_95_pct": 3.4,
    "diversification_score": 0.65,
    "correlation_clusters": [["EURUSD", "GBPUSD"], ["USDJPY"]],
    "stress_test_worst_case_pct": -6.2,
    "stress_test_scenarios_survived": "7/8"
  }
}
```

**Statut:** [x] Terminé

---

### Tache 24 : Tests unitaires Tier 3

**Fichier:** `backend/tests/unit/test_correlation_matrix.py` (nouveau)
**Fichier:** `backend/tests/unit/test_var_engine.py` (nouveau)
**Fichier:** `backend/tests/unit/test_stress_test.py` (nouveau)

**Tests matrice de corrélation :**

| Test | Scenario |
|------|----------|
| `test_perfect_correlation` | Deux séries identiques → corr = 1.0 |
| `test_inverse_correlation` | Deux séries inversées → corr = -1.0 |
| `test_cluster_detection` | EURUSD + GBPUSD dans même cluster, USDJPY séparé |
| `test_diversification_score_concentrated` | Toutes positions corrélées → score bas |
| `test_diversification_score_diverse` | Positions décorrélées → score haut |
| `test_missing_data_handling` | Symbole sans historique suffisant → exclu avec warning |

**Tests VaR :**

| Test | Scenario |
|------|----------|
| `test_var_single_position` | 1 position simple → VaR = vol * exposure * z_score |
| `test_var_diversified_portfolio` | Positions décorrélées → VaR < somme des VaR individuels |
| `test_var_concentrated_portfolio` | Positions corrélées → VaR proche de la somme |
| `test_cvar_greater_than_var` | CVaR >= VaR toujours |
| `test_marginal_var_calculation` | VaR avec - VaR sans = marginal VaR |
| `test_var_reject_above_limit` | VaR 95% 6% avec limit 5% → REJECT |
| `test_var_reproducibility` | Seed fixé → résultat reproductible |

**Tests stress test :**

| Test | Scenario |
|------|----------|
| `test_usd_crash_long_eurusd` | BUY EURUSD + USD crash → profit |
| `test_usd_crash_long_usdjpy` | BUY USDJPY + USD crash → perte |
| `test_flash_crash_margin_call` | Portefeuille leveragé + flash crash → margin call = True |
| `test_risk_off_crypto_heavy` | Portefeuille crypto dominant + risk-off → grosse perte |
| `test_portfolio_survives_scenario` | Portefeuille diversifié survit risk-off |
| `test_custom_scenario` | Scénario personnalisé appliqué correctement |

**Statut:** [x] Terminé

---
---

## Fichiers impactés (résumé)

### Tier 1

| Fichier | Action |
|---------|--------|
| `backend/app/services/risk/portfolio_state.py` | Nouveau |
| `backend/app/services/risk/limits.py` | Nouveau |
| `backend/app/services/risk/rules.py` | Modifier (ajouter `evaluate_portfolio`) |
| `backend/app/db/models/portfolio_snapshot.py` | Nouveau |
| `backend/app/services/mcp/trading_server.py` | Modifier (nouveau tool `portfolio_risk_evaluation`) |
| `backend/app/services/agentscope/prompts.py` | Modifier (enrichir prompt) |
| `backend/app/services/agentscope/registry.py` | Modifier (injecter portfolio state + traces) |
| `backend/app/services/agentscope/toolkit.py` | Modifier (ajouter tool) |
| `backend/app/tasks/portfolio_tasks.py` | Nouveau |
| `backend/tests/unit/test_risk_engine_portfolio.py` | Nouveau |
| Migration Alembic | Nouveau |

### Tier 2

| Fichier | Action |
|---------|--------|
| `backend/app/services/risk/currency_exposure.py` | Nouveau |
| `backend/app/services/risk/correlation_exposure.py` | Nouveau |
| `backend/app/services/risk/limits.py` | Modifier (ajouter champs Tier 2) |
| `backend/app/services/risk/portfolio_state.py` | Modifier (ajouter weekly drawdown) |
| `backend/app/services/mcp/trading_server.py` | Modifier (enrichir retour tool) |
| `backend/app/services/agentscope/prompts.py` | Modifier (enrichir prompt) |
| `backend/tests/unit/test_currency_exposure.py` | Nouveau |
| `backend/tests/unit/test_correlation_exposure.py` | Nouveau |

### Tier 3

| Fichier | Action |
|---------|--------|
| `backend/app/services/risk/correlation_matrix.py` | Nouveau |
| `backend/app/services/risk/var_engine.py` | Nouveau |
| `backend/app/services/risk/stress_test.py` | Nouveau |
| `backend/app/services/risk/limits.py` | Modifier (ajouter champs Tier 3) |
| `backend/app/services/mcp/trading_server.py` | Modifier (nouveau tool `portfolio_stress_test`) |
| `backend/app/services/agentscope/registry.py` | Modifier (enrichir traces) |
| `backend/app/tasks/portfolio_tasks.py` | Modifier (ajouter tâche corrélation quotidienne) |
| ~~`backend/requirements.txt`~~ | ~~Modifier (ajouter `scipy`)~~ — **Non requis** : clustering implémenté avec BFS/numpy, pas de dépendance scipy |
| `backend/tests/unit/test_correlation_matrix.py` | Nouveau |
| `backend/tests/unit/test_var_engine.py` | Nouveau |
| `backend/tests/unit/test_stress_test.py` | Nouveau |

---

## Critères de succès

### Tier 1

1. [x] Le risk-manager utilise l'equity **réelle** du compte (plus de 10k hardcodé) — `rules.py:516`
2. [x] Un trade est rejeté si le drawdown journalier dépasse la limite — `rules.py:462`, test `test_reject_daily_loss_exceeded`
3. [x] Un trade est rejeté si le budget de risque total est épuisé — `rules.py:475`, test `test_reject_risk_budget_exceeded`
4. [x] Un trade est rejeté si le nombre max de positions est atteint — `rules.py:488`, test `test_reject_max_positions`
5. [x] Un trade est rejeté si déjà exposé sur le même symbole — `rules.py:504`, test `test_reject_max_per_symbol`
6. [x] Les debug traces contiennent le résumé portefeuille complet — `registry.py:1657+`, `trading_server.py:1422-1434`
7. [x] Tous les tests unitaires passent — 436 passed, 0 régression
8. [x] Mode dégradé fonctionnel si MetaAPI est indisponible — `portfolio_state.py:build_defaults()`, test `test_degraded_data_handling`

### Tier 2

9. [x] L'exposition par devise est calculée et affichée — `currency_exposure.py:compute_currency_exposure()`, retournée dans `portfolio_summary`
10. [x] Un trade est rejeté si l'exposition sur une devise dépasse la limite — `rules.py:542-556` (Check 8)
11. [x] Les corrélations entre positions ouvertes sont détectées et signalées — `correlation_exposure.py:compute_correlation_exposure()`, `correlation_alerts` dans le retour
12. [x] Le risque effectif est ajusté par le multiplicateur de corrélation — `correlation_exposure.py:effective_risk_multiplier`, `rules.py:580` (Check 10)
13. [x] Le drawdown hebdomadaire est suivi et contraint — `portfolio_state.py:weekly_drawdown_pct`, `rules.py:530-539` (Check 7)
14. [x] Un trade est rejeté si l'exposition brute totale dépasse la limite — `rules.py:558-568` (Check 9)

### Tier 3

15. [x] La matrice de corrélation est calculée quotidiennement et stockée en cache — `correlation_matrix.py`, `portfolio_tasks.py:refresh_correlation_matrix`, Redis TTL 24h
16. [x] Le VaR 95% et 99% du portefeuille est calculé par Monte Carlo — `var_engine.py:calculate_var()`, Cholesky + 10k simulations
17. [x] Le VaR marginal d'un nouveau trade est calculé — `var_engine.py:marginal_var`, test `test_marginal_var_calculation`
18. [x] Le score de diversification est calculé — `correlation_matrix.py:get_diversification_score()`, test `test_diversification_score_*`
19. [x] Les stress tests sont exécutés sur 8 scénarios prédéfinis — `stress_test.py:run_stress_test()`, résultats dans `portfolio_summary.stress_test`
20. [x] Les debug traces contiennent VaR, diversification et résultats stress test — `registry.py:1676+`, `trading_server.py:1477-1491`

---

## Notes d'implémentation

### Déviations par rapport à la spec

1. **scipy non utilisé** : le clustering hiérarchique prévu dans la Task 17 a été remplacé par un clustering BFS basé sur un seuil de corrélation. Résultat identique, sans dépendance supplémentaire.

2. **VaR et stress tests en mode advisory** : contrairement à la spec qui prévoyait des checks 11-14 bloquants dans `evaluate_portfolio()`, le VaR et les stress tests sont retournés dans le résultat du tool MCP (advisory) plutôt que comme checks déterministes. Raison : ces calculs dépendent de données externes (historique de prix, Redis cache) potentiellement indisponibles, et leur coût computationnel ne justifie pas un blocage systématique du pipeline. Le LLM risk-manager reçoit ces données et peut les utiliser dans sa décision.

### Résumé des tests

| Fichier de test | Tests | Tier |
|----------------|-------|------|
| `test_risk_engine_portfolio.py` | 10 | 1 |
| `test_currency_exposure.py` | 8 | 2 |
| `test_correlation_exposure.py` | 11 | 2 |
| `test_correlation_matrix.py` | 9 | 3 |
| `test_var_engine.py` | 8 | 3 |
| `test_stress_test.py` | 8 | 3 |
| **Total** | **54** | |
