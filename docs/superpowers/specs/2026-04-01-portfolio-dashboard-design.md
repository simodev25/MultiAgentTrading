# Portfolio Dashboard — Temps Réel

**Date:** 2026-04-01
**Statut:** Termine
**Objectif:** Créer une page `/portfolio` dédiée à la surveillance temps réel du portefeuille : equity, drawdown, budget de risque, exposition devise, et stress test. Données poussées via WebSocket toutes les 10s.

---

## Architecture

```
Backend:
  GET  /portfolio/state     → PortfolioState complet (snapshot courant)
  GET  /portfolio/history    → snapshots DB (equity curve, filtre période)
  GET  /portfolio/stress     → stress test résultats
  WS   /ws/portfolio         → push toutes les 10s

Frontend:
  /portfolio                 → PortfolioPage.tsx
  usePortfolioStream.ts      → hook WebSocket + fallback REST
  5 composants dans portfolio/
```

---

## Tache 1 : Backend — API REST portfolio

**Fichier:** `backend/app/api/routes/portfolio.py` (nouveau)

### Endpoint 1 : GET /portfolio/state

Retourne le PortfolioState courant enrichi avec les limites effectives.

```python
@router.get('/state')
async def portfolio_state(
    account_ref: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    # Appelle PortfolioStateService.get_current_state()
    # Retourne: state + limits effectifs + currency_exposure
```

**Retour :**
```json
{
  "state": {
    "balance": 9850.0,
    "equity": 9920.0,
    "free_margin": 6200.0,
    "used_margin": 3720.0,
    "leverage": 100,
    "open_position_count": 2,
    "open_risk_total_pct": 3.5,
    "daily_realized_pnl": -45.0,
    "daily_unrealized_pnl": 70.0,
    "daily_drawdown_pct": 1.2,
    "weekly_drawdown_pct": 2.8,
    "daily_high_equity": 10040.0,
    "degraded": false
  },
  "limits": {
    "max_daily_loss_pct": 3.0,
    "max_weekly_loss_pct": 5.0,
    "max_open_risk_pct": 6.0,
    "max_positions": 3,
    "min_free_margin_pct": 50.0,
    "max_currency_exposure_pct": 15.0
  },
  "currency_exposure": {
    "EUR": {"net_lots": 0.3, "exposure_pct": 12.5},
    "USD": {"net_lots": -0.5, "exposure_pct": 22.0},
    "GBP": {"net_lots": 0.1, "exposure_pct": 4.2}
  },
  "open_positions": [
    {"symbol": "EURUSD.PRO", "side": "BUY", "volume": 0.3, "pnl": 45.0, "risk_pct": 1.8},
    {"symbol": "GBPUSD.PRO", "side": "BUY", "volume": 0.2, "pnl": 25.0, "risk_pct": 1.7}
  ]
}
```

**Statut:** [x] Termine

### Endpoint 2 : GET /portfolio/history

Retourne les snapshots historiques pour l'equity curve.

```python
@router.get('/history')
def portfolio_history(
    period: str = Query(default='7d'),  # 24h | 7d | 30d
    db: Session = Depends(get_db),
) -> dict:
    # Requête portfolio_snapshots filtrée par période
    # Retourne: liste de {timestamp, equity, balance, drawdown_pct}
```

**Statut:** [x] Termine

### Endpoint 3 : GET /portfolio/stress

Retourne les résultats du stress test sur le portefeuille courant.

```python
@router.get('/stress')
async def portfolio_stress(
    account_ref: int | None = None,
) -> dict:
    # Appelle run_stress_test() sur les positions courantes
    # Retourne: StressTestReport sérialisé
```

**Statut:** [x] Termine

---

## Tache 2 : Backend — WebSocket /ws/portfolio

**Fichier:** `backend/app/api/ws/portfolio_stream.py` (nouveau)

WebSocket qui pousse un update toutes les 10 secondes :

```python
@router.websocket('/ws/portfolio')
async def portfolio_stream(websocket: WebSocket):
    await websocket.accept()
    while True:
        state = await PortfolioStateService.get_current_state(...)
        limits = get_risk_limits(mode)
        currency = compute_currency_exposure(state.open_positions, state.equity)
        await websocket.send_json({
            "type": "portfolio_update",
            "state": { ... },
            "limits": { ... },
            "currency_exposure": { ... },
            "timestamp": datetime.now(UTC).isoformat(),
        })
        await asyncio.sleep(10)
```

Authentification via query param `?token=` (même pattern que `wsMarketPricesUrl`).

**Statut:** [x] Termine

---

## Tache 3 : Backend — Enregistrer les routes

**Fichier:** `backend/app/main.py` (modifier)

Ajouter le router portfolio et le WebSocket.

**Statut:** [x] Termine

---

## Tache 4 : Frontend — Hook usePortfolioStream

**Fichier:** `frontend/src/hooks/usePortfolioStream.ts` (nouveau)

Hook qui :
1. Ouvre un WebSocket vers `/ws/portfolio?token=...`
2. Met à jour le state à chaque message
3. Reconnexion automatique avec backoff exponentiel (1s, 2s, 4s, max 30s)
4. Fallback REST (`GET /portfolio/state`) si WS échoue pendant > 30s

```typescript
export function usePortfolioStream(): {
  state: PortfolioState | null;
  limits: RiskLimits | null;
  currencyExposure: Record<string, CurrencyExposure>;
  connected: boolean;
  lastUpdate: string;
}
```

**Statut:** [x] Termine

---

## Tache 5 : Frontend — PortfolioPage

**Fichier:** `frontend/src/pages/PortfolioPage.tsx` (nouveau)

Page principale composée des 5 sections. Layout vertical avec le design system existant (dark theme, `hw-surface`, Tailwind).

```
┌─────────────────────────────────────────────────┐
│  PORTFOLIO DASHBOARD              ● connected   │
├─────────────────────────────────────────────────┤
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │
│ │Equity│ │Balance│ │PnL   │ │DD    │ │DD    │  │
│ │$9,920│ │$9,850│ │+$25  │ │1.2%  │ │2.8%  │  │
│ │      │ │      │ │+0.25%│ │daily │ │weekly│  │
│ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘  │
│ ┌──────┐                                       │
│ │Pos.  │                                       │
│ │ 2/3  │                                       │
│ └──────┘                                       │
├─────────────────────────────────────────────────┤
│ EQUITY CURVE                    [24h][7d][30d]  │
│ ┌───────────────────────────────────────────┐   │
│ │  📈 LineChart (MUI X-Charts)              │   │
│ │  Ligne bleue = equity                     │   │
│ │  Zone rouge translucide = drawdown        │   │
│ └───────────────────────────────────────────┘   │
├─────────────────────────────────────────────────┤
│ RISK BUDGET                                     │
│ Open Risk:     ████████░░░░░  3.5% / 6.0%      │
│ Daily DD:      ████░░░░░░░░░  1.2% / 3.0%      │
│ Weekly DD:     ██████░░░░░░░  2.8% / 5.0%      │
│ Positions:     ████████░░░░░  2 / 3             │
│ Margin Used:   ██████░░░░░░░  37.5% / 50.0%    │
├─────────────────────────────────────────────────┤
│ CURRENCY EXPOSURE                               │
│ ┌───────────────────────────────────────────┐   │
│ │  BarChart horizontal                      │   │
│ │  EUR ████████  +12.5%                     │   │
│ │  USD ████████████████  -22.0%  ⚠ >15%    │   │
│ │  GBP ████  +4.2%                          │   │
│ │  --- limite pointillée à 15% ---          │   │
│ └───────────────────────────────────────────┘   │
├─────────────────────────────────────────────────┤
│ STRESS TEST                                     │
│ ┌───────────────────────────────────────────┐   │
│ │ Scenario         │ PnL     │ Status       │   │
│ │ USD Crash         │ +$120  │ ● survived   │   │
│ │ Risk-Off          │ -$340  │ ● survived   │   │
│ │ Flash Crash       │ -$890  │ ● margin call│   │
│ │ ...               │        │              │   │
│ ├───────────────────────────────────────────┤   │
│ │ 7/8 survived — recommendation: SAFE      │   │
│ └───────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

**Statut:** [x] Termine

---

## Tache 6 : Frontend — PortfolioKPIs

**Fichier:** `frontend/src/components/portfolio/PortfolioKPIs.tsx` (nouveau)

6 cartes KPI alignées horizontalement :

| KPI | Source | Format | Couleur |
|-----|--------|--------|---------|
| Equity | `state.equity` | `$9,920` + delta vs daily_high | vert si > daily_high, rouge sinon |
| Balance | `state.balance` | `$9,850` | neutre |
| PnL du jour | `daily_realized + daily_unrealized` | `+$25 (+0.25%)` | vert/rouge |
| DD journalier | `state.daily_drawdown_pct` | `1.2%` | led vert/orange/rouge selon % de la limite |
| DD hebdo | `state.weekly_drawdown_pct` | `2.8%` | idem |
| Positions | `count / max` | `2/3` | mini barre de progression |

Seuils LED :
- < 50% de la limite → vert
- 50-80% → orange
- > 80% → rouge

**Statut:** [x] Termine

---

## Tache 7 : Frontend — EquityCurveChart

**Fichier:** `frontend/src/components/portfolio/EquityCurveChart.tsx` (nouveau)

- MUI `LineChart` avec `AreaPlot` pour le drawdown
- Données : `GET /portfolio/history?period=7d`
- 3 boutons période : 24h, 7d, 30d
- Ligne bleue = equity
- Zone rouge translucide = drawdown (entre equity et daily_high)
- Hauteur : 280px
- Responsive
- Style MUI dark theme (existant dans `RealTradesCharts.tsx`)

**Statut:** [x] Termine

---

## Tache 8 : Frontend — RiskBudgetBars

**Fichier:** `frontend/src/components/portfolio/RiskBudgetBars.tsx` (nouveau)

5 barres de progression avec :
- Label à gauche
- Barre remplie proportionnellement
- Valeur / Max à droite
- Couleur dynamique : vert → orange → rouge

| Barre | Valeur | Max | Sens |
|-------|--------|-----|------|
| Open Risk | `open_risk_total_pct` | `max_open_risk_pct` | normal |
| Daily DD | `daily_drawdown_pct` | `max_daily_loss_pct` | normal |
| Weekly DD | `weekly_drawdown_pct` | `max_weekly_loss_pct` | normal |
| Positions | `open_position_count` | `max_positions` | normal |
| Margin Used | `used_margin / equity * 100` | `100 - min_free_margin_pct` | normal |

Couleur : `pct < 50% → #00D26A, 50-80% → #FFA502, > 80% → #FF4757`

**Statut:** [x] Termine

---

## Tache 9 : Frontend — CurrencyExposureChart

**Fichier:** `frontend/src/components/portfolio/CurrencyExposureChart.tsx` (nouveau)

- MUI `BarChart` horizontal
- Barres positives (long) en `#4B7BF5` (accent), négatives en `#FF4757` (danger)
- Ligne pointillée verticale au seuil `max_currency_exposure_pct`
- Badge warning si une devise dépasse le seuil
- Hauteur dynamique selon le nombre de devises

**Statut:** [x] Termine

---

## Tache 10 : Frontend — StressTestTable

**Fichier:** `frontend/src/components/portfolio/StressTestTable.tsx` (nouveau)

- Tableau avec les colonnes : Scenario, Description, PnL ($), PnL (%), Survie
- Badge survie : `● survived` (vert) ou `● failed` / `● margin call` (rouge)
- Ligne résumé en bas : `"7/8 survived"` + recommendation badge (safe=vert, reduce=orange, critical=rouge)
- Données : `GET /portfolio/stress` (appelé au mount, pas en WS — trop coûteux)

**Statut:** [x] Termine

---

## Tache 11 : Frontend — Route + Navigation

**Fichier:** `frontend/src/App.tsx` (modifier)

Ajouter la route :
```tsx
<Route path="/portfolio" element={<PortfolioPage />} />
```

Ajouter le lien dans la navigation (sidebar/header existant).

**Statut:** [x] Termine

---

## Fichiers impactés (résumé)

### Backend (3 nouveaux, 1 modifié)

| Fichier | Action |
|---------|--------|
| `backend/app/api/routes/portfolio.py` | Nouveau — 3 endpoints REST |
| `backend/app/api/ws/portfolio_stream.py` | Nouveau — WebSocket handler |
| `backend/app/main.py` | Modifier — enregistrer routes |

### Frontend (8 nouveaux, 1 modifié)

| Fichier | Action |
|---------|--------|
| `frontend/src/pages/PortfolioPage.tsx` | Nouveau — page principale |
| `frontend/src/hooks/usePortfolioStream.ts` | Nouveau — hook WS |
| `frontend/src/components/portfolio/PortfolioKPIs.tsx` | Nouveau |
| `frontend/src/components/portfolio/EquityCurveChart.tsx` | Nouveau |
| `frontend/src/components/portfolio/RiskBudgetBars.tsx` | Nouveau |
| `frontend/src/components/portfolio/CurrencyExposureChart.tsx` | Nouveau |
| `frontend/src/components/portfolio/StressTestTable.tsx` | Nouveau |
| `frontend/src/App.tsx` | Modifier — route + nav |

---

## Critères de succès

1. [ ] La page `/portfolio` affiche les 6 KPIs avec données temps réel
2. [ ] L'equity curve affiche l'historique sur 24h/7d/30d avec zone drawdown
3. [ ] Les 5 barres de budget risque changent de couleur selon les seuils (vert/orange/rouge)
4. [ ] L'exposition devise affiche les barres long/short avec la ligne de limite
5. [ ] Le tableau stress test affiche les 8 scénarios avec badges survie
6. [ ] Le WebSocket pousse les mises à jour toutes les 10s
7. [ ] Reconnexion automatique du WS avec backoff exponentiel
8. [ ] Fallback REST si le WS échoue pendant > 30s
9. [ ] Le design respecte le dark theme existant (couleurs, fonts, composants)
10. [ ] La page est accessible depuis la navigation principale
