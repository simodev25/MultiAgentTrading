# Custom Strategy Engine — V2 Design (Future)

**Date:** 2026-04-04
**Status:** Draft — à implémenter après validation de la v1 avec les 20 templates
**Priority:** Low — les 20 templates couvrent 90% des cas

---

## Problème

Les 20 templates actuels obligent le LLM à choisir parmi des stratégies prédéfinies. Le LLM ne peut pas combiner librement des indicateurs (ex: RSI + VWAP + Ichimoku), ni inventer de nouvelles logiques de trading.

## Solution proposée

### Moteur de règles composables

Le LLM génère un JSON structuré de conditions au lieu de choisir un template :

```json
{
  "type": "custom",
  "name": "RSI_VWAP_Ichimoku_Combo",
  "description": "Buy when RSI oversold + price below VWAP + above Ichimoku cloud",
  "entry_buy": [
    {"indicator": "RSI", "params": {"period": 14}, "condition": "<", "value": 25},
    {"indicator": "MACD", "params": {"fast": 12, "slow": 26, "signal": 9}, "condition": "cross_above", "reference": "signal"},
    {"indicator": "price", "condition": ">", "reference": "ichimoku_cloud_top"}
  ],
  "entry_sell": [
    {"indicator": "RSI", "params": {"period": 14}, "condition": ">", "value": 75},
    {"indicator": "price", "condition": "<", "reference": "VWAP"}
  ],
  "exit": {
    "type": "atr_trailing",
    "params": {"period": 14, "multiplier": 2.0}
  },
  "filters": [
    {"indicator": "ADX", "params": {"period": 14}, "condition": ">", "value": 20, "note": "only trade in trending market"}
  ]
}
```

### Rôle du LLM

```
User: "Je veux acheter quand RSI en survente et MACD confirme le retournement"
  │
  └─→ LLM traduit en JSON de règles (pas de template, combinaison libre)
      │
      └─→ Rule Engine évalue les conditions sur les données
          │
          ├─→ Backtest: évalue sur historique
          ├─→ Monitoring: calcule signal en temps réel
          └─→ Chart: affiche overlays + signaux
```

Le LLM est le **traducteur** langage humain → règles structurées.

### Bibliothèque d'indicateurs disponibles

Le moteur de règles supporterait tous les indicateurs de la lib `ta` :

**Trend:** EMA, SMA, MACD, ADX, DI+, DI-, Ichimoku (Tenkan, Kijun, Senkou A/B), Parabolic SAR, Supertrend, Aroon
**Momentum:** RSI, Stochastic, Williams %R, CCI, ROC, MFI, TSI
**Volatility:** Bollinger Bands, Keltner Channel, ATR, Donchian Channel
**Volume:** VWAP, OBV, CMF, Volume SMA
**Price:** SMA, High/Low of N bars, Pivot Points

### Opérateurs de condition

| Opérateur | Description | Exemple |
|-----------|-------------|---------|
| `<` / `>` / `<=` / `>=` | Comparaison à une valeur | RSI < 25 |
| `cross_above` / `cross_below` | Croisement | MACD cross_above signal |
| `above` / `below` | Position relative | price above ichimoku_cloud |
| `between` | Range | RSI between 40-60 |
| `increasing` / `decreasing` | Direction sur N bars | ADX increasing over 3 bars |

### Architecture technique

```
┌─────────────────────────────────────────┐
│          LLM Strategy Designer          │
│  (traduit prompt → JSON de règles)      │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│           Rule Validator                │
│  (vérifie que les indicateurs existent, │
│   que les params sont valides,          │
│   que les conditions sont cohérentes)   │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│          Rule Engine                    │
│  (évalue les conditions sur les OHLC)   │
│                                         │
│  Pour chaque barre:                     │
│    1. Calcule tous les indicateurs      │
│    2. Évalue chaque condition           │
│    3. Si TOUTES les conditions entry    │
│       sont true → signal BUY/SELL      │
│    4. Évalue les conditions exit        │
│       → ferme la position              │
└───────────────┬─────────────────────────┘
                │
        ┌───────┼───────┐
        ▼       ▼       ▼
    Backtest  Monitor  Chart
```

### Fichiers à créer

| Fichier | Rôle |
|---------|------|
| `services/strategy/rule_engine.py` | Évaluateur de règles — calcule les indicateurs et évalue les conditions |
| `services/strategy/rule_validator.py` | Validation des règles JSON — vérifie la cohérence |
| `services/strategy/indicator_registry.py` | Catalogue de tous les indicateurs disponibles avec params |
| `schemas/strategy_rules.py` | Pydantic models pour le JSON de règles |
| `api/routes/strategies.py` | Nouvelle route pour les stratégies custom |

### Compatibilité

- Les 20 templates existants continuent de fonctionner tels quels
- Les stratégies custom sont un **type supplémentaire** (`type: "custom"` vs `type: "template"`)
- Le backtest engine accepte les deux types
- Le monitoring accepte les deux types
- Le chart overlay accepte les deux types

### Estimation

| Tâche | Effort |
|-------|--------|
| Rule Engine (évaluation conditions) | 1-2 jours |
| Indicator Registry (catalogue complet) | 0.5 jour |
| Rule Validator | 0.5 jour |
| Integration backtest/monitoring/chart | 1 jour |
| LLM prompt pour génération de règles | 0.5 jour |
| Tests | 0.5 jour |
| **Total** | **~4-5 jours** |

### Quand le faire

Quand les 20 templates ne suffisent plus — typiquement quand des utilisateurs demandent des combinaisons spécifiques qui ne matchent aucun template. Pour l'instant, les templates couvrent 90% des cas.
