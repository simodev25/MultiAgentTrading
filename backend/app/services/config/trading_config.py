"""Runtime trading configuration — resolves parameters from DB > env > code defaults.

Provides configurable decision gating thresholds, risk limits, and trade sizing
multipliers. All values can be overridden at runtime via the ConnectorConfig
'trading' connector without restarting the application.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.services.agentscope.constants import (
    DECISION_MODES,
    DecisionGatingPolicy,
)
from app.services.risk.limits import RISK_LIMITS, RiskLimits

CONNECTOR_NAME = "trading"

# ── Parameter catalog with descriptions ──
# Each entry: (key, description, type, default_per_mode)

GATING_PARAMS: list[dict[str, Any]] = [
    {
        "key": "min_combined_score",
        "label": "Min Combined Score",
        "description": "Score minimum pour declencher un trade. Plus c'est haut, moins de trades sont pris.",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
    },
    {
        "key": "min_confidence",
        "label": "Min Confidence",
        "description": "Niveau de confiance minimum requis. En dessous, le trade est bloque meme si le score est bon.",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
    },
    {
        "key": "min_aligned_sources",
        "label": "Min Aligned Sources",
        "description": "Nombre minimum d'agents (tech, news, context) qui doivent etre d'accord sur la direction. 1 = un seul agent suffit, 2 = consensus requis.",
        "type": "int",
        "min": 0,
        "max": 3,
        "step": 1,
    },
    {
        "key": "allow_technical_single_source_override",
        "label": "Allow Technical Override",
        "description": "Si actif, l'analyse technique seule peut declencher un trade meme si news et context sont neutres.",
        "type": "bool",
    },
]

RISK_PARAMS: list[dict[str, Any]] = [
    {
        "key": "max_risk_per_trade_pct",
        "label": "Max Risk Per Trade (%)",
        "description": "Risque maximum par trade en pourcentage de l'equity. Ex: 2% = on risque max 200 sur un compte de 10 000.",
        "type": "float",
        "min": 0.1,
        "max": 10.0,
        "step": 0.1,
    },
    {
        "key": "max_daily_loss_pct",
        "label": "Max Daily Loss (%)",
        "description": "Perte maximale autorisee sur une journee. Au-dela, tous les trades sont bloques jusqu'au lendemain.",
        "type": "float",
        "min": 0.5,
        "max": 20.0,
        "step": 0.5,
    },
    {
        "key": "max_open_risk_pct",
        "label": "Max Open Risk (%)",
        "description": "Risque total maximum de toutes les positions ouvertes combinees. Empeche la surexposition.",
        "type": "float",
        "min": 1.0,
        "max": 30.0,
        "step": 0.5,
    },
    {
        "key": "max_positions",
        "label": "Max Positions",
        "description": "Nombre maximum de positions ouvertes en meme temps. Limite la complexite du portefeuille.",
        "type": "int",
        "min": 1,
        "max": 20,
        "step": 1,
    },
    {
        "key": "max_positions_per_symbol",
        "label": "Max Positions Per Symbol",
        "description": "Nombre maximum de positions sur le meme instrument. 1 = une seule position par paire.",
        "type": "int",
        "min": 1,
        "max": 5,
        "step": 1,
    },
    {
        "key": "min_free_margin_pct",
        "label": "Min Free Margin (%)",
        "description": "Pourcentage minimum de marge libre requis. Protege contre le margin call en gardant une reserve.",
        "type": "float",
        "min": 5.0,
        "max": 80.0,
        "step": 5.0,
    },
    {
        "key": "max_currency_notional_exposure_pct_warn",
        "label": "Max Currency Notional Exposure Warn (%)",
        "description": "Seuil d'alerte de concentration notionnelle par devise. Mesure de concentration, pas de risque stop-based.",
        "type": "float",
        "min": 5.0,
        "max": 300.0,
        "step": 5.0,
    },
    {
        "key": "max_currency_notional_exposure_pct_block",
        "label": "Max Currency Notional Exposure Block (%)",
        "description": "Seuil de blocage dur de concentration notionnelle par devise. A utiliser avec prudence pendant la transition.",
        "type": "float",
        "min": 5.0,
        "max": 500.0,
        "step": 5.0,
    },
    {
        "key": "max_currency_open_risk_pct",
        "label": "Max Currency Open Risk (%)",
        "description": "Risque stop-based agrege par devise. Expose pour observabilite en phase 1, pas encore utilise comme hard gate.",
        "type": "float",
        "min": 1.0,
        "max": 100.0,
        "step": 1.0,
    },
    {
        "key": "max_weekly_loss_pct",
        "label": "Max Weekly Loss (%)",
        "description": "Perte maximale autorisee sur une semaine. Au-dela, les trades sont bloques jusqu'a la semaine suivante.",
        "type": "float",
        "min": 1.0,
        "max": 30.0,
        "step": 1.0,
    },
]

SIZING_PARAMS: list[dict[str, Any]] = [
    {
        "key": "sl_atr_multiplier",
        "label": "Stop Loss ATR Multiplier",
        "description": "Multiplicateur ATR pour le stop loss. Ex: 1.5 = SL place a 1.5x la volatilite moyenne. Plus c'est haut, plus le SL est loin.",
        "type": "float",
        "min": 0.5,
        "max": 5.0,
        "step": 0.1,
    },
    {
        "key": "tp_atr_multiplier",
        "label": "Take Profit ATR Multiplier",
        "description": "Multiplicateur ATR pour le take profit. Ex: 2.5 = TP place a 2.5x la volatilite. Ratio R:R = TP/SL (2.5/1.5 = 1.67).",
        "type": "float",
        "min": 0.5,
        "max": 10.0,
        "step": 0.1,
    },
    {
        "key": "min_sl_distance_pct",
        "label": "Min SL Distance (%)",
        "description": "Distance minimum du stop loss en % du prix. En dessous, le trade est refuse (SL trop serre). Baisser pour les petits timeframes (M5/M15). Ex: 0.02 = SL doit etre a au moins 0.02% du prix.",
        "type": "float",
        "min": 0.005,
        "max": 0.5,
        "step": 0.005,
    },
]


def _get_runtime_settings() -> dict[str, Any]:
    """Load trading runtime settings from ConnectorConfig DB (cached 5s)."""
    try:
        from app.services.connectors.runtime_settings import RuntimeConnectorSettings
        return RuntimeConnectorSettings.settings(CONNECTOR_NAME)
    except Exception:
        return {}


def get_effective_gating_policy(mode: str) -> DecisionGatingPolicy:
    """Resolve DecisionGatingPolicy: DB overrides > code defaults for the given mode."""
    base = DECISION_MODES.get(mode, DECISION_MODES["balanced"])
    runtime = _get_runtime_settings()

    gating_overrides = runtime.get("gating", {})
    if not isinstance(gating_overrides, dict):
        return base

    # Build override dict from runtime settings
    overrides: dict[str, Any] = {}
    for param in GATING_PARAMS:
        key = param["key"]
        if key in gating_overrides:
            try:
                if param["type"] == "float":
                    overrides[key] = float(gating_overrides[key])
                elif param["type"] == "int":
                    overrides[key] = int(gating_overrides[key])
                elif param["type"] == "bool":
                    overrides[key] = bool(gating_overrides[key])
            except (TypeError, ValueError):
                pass

    if not overrides:
        return base

    # Merge: override fields on top of base
    base_dict = {
        "min_combined_score": base.min_combined_score,
        "min_confidence": base.min_confidence,
        "min_aligned_sources": base.min_aligned_sources,
        "allow_technical_single_source_override": base.allow_technical_single_source_override,
        "block_major_contradiction": base.block_major_contradiction,
        "contradiction_penalty_weak": base.contradiction_penalty_weak,
        "contradiction_penalty_moderate": base.contradiction_penalty_moderate,
        "contradiction_penalty_major": base.contradiction_penalty_major,
        "confidence_multiplier_moderate": base.confidence_multiplier_moderate,
        "confidence_multiplier_major": base.confidence_multiplier_major,
    }
    base_dict.update(overrides)
    return DecisionGatingPolicy(**base_dict)


def get_effective_risk_limits(mode: str) -> RiskLimits:
    """Resolve RiskLimits: DB overrides > code defaults for the given execution mode."""
    base = RISK_LIMITS.get(mode, RISK_LIMITS["live"])
    runtime = _get_runtime_settings()

    risk_overrides = runtime.get("risk_limits", {})
    if not isinstance(risk_overrides, dict):
        return base

    overrides: dict[str, Any] = {}
    for param in RISK_PARAMS:
        key = param["key"]
        if key in risk_overrides:
            try:
                if param["type"] == "float":
                    overrides[key] = float(risk_overrides[key])
                elif param["type"] == "int":
                    overrides[key] = int(risk_overrides[key])
            except (TypeError, ValueError):
                pass

    if not overrides:
        return base

    base_dict = asdict(base)
    base_dict.update(overrides)
    # frozen dataclass — rebuild
    return RiskLimits(**base_dict)


def get_effective_sizing() -> dict[str, float]:
    """Resolve trade sizing ATR multipliers: DB overrides > code defaults."""
    from app.services.agentscope.constants import SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER

    defaults: dict[str, float] = {
        "sl_atr_multiplier": SL_ATR_MULTIPLIER,
        "tp_atr_multiplier": TP_ATR_MULTIPLIER,
        "min_sl_distance_pct": 0.05,
    }
    runtime = _get_runtime_settings()
    sizing_overrides = runtime.get("sizing", {})
    if not isinstance(sizing_overrides, dict):
        return defaults

    for key in defaults:
        if key in sizing_overrides:
            try:
                defaults[key] = float(sizing_overrides[key])
            except (TypeError, ValueError):
                pass

    return defaults


def get_param_catalog() -> dict[str, list[dict[str, Any]]]:
    """Return full parameter catalog with descriptions for frontend rendering."""
    return {
        "gating": GATING_PARAMS,
        "risk_limits": RISK_PARAMS,
        "sizing": SIZING_PARAMS,
    }


def get_active_config_version(db: Any = None) -> int:
    """Return the latest trading config version number, or 0 if none."""
    if db is None:
        try:
            from app.db.session import SessionLocal
            db = SessionLocal()
            try:
                return _query_max_version(db)
            finally:
                db.close()
        except Exception:
            return 0
    return _query_max_version(db)


def _query_max_version(db: Any) -> int:
    try:
        from app.db.models.trading_config_version import TradingConfigVersion
        from sqlalchemy import func
        result = db.query(func.max(TradingConfigVersion.version)).scalar()
        return result or 0
    except Exception:
        return 0


def get_current_values(decision_mode: str, execution_mode: str) -> dict[str, dict[str, Any]]:
    """Return current effective values for all configurable parameters."""
    gating = get_effective_gating_policy(decision_mode)
    limits = get_effective_risk_limits(execution_mode)
    sizing = get_effective_sizing()

    return {
        "gating": {
            "min_combined_score": gating.min_combined_score,
            "min_confidence": gating.min_confidence,
            "min_aligned_sources": gating.min_aligned_sources,
            "allow_technical_single_source_override": gating.allow_technical_single_source_override,
        },
        "risk_limits": {
            "max_risk_per_trade_pct": limits.max_risk_per_trade_pct,
            "max_daily_loss_pct": limits.max_daily_loss_pct,
            "max_open_risk_pct": limits.max_open_risk_pct,
            "max_positions": limits.max_positions,
            "max_positions_per_symbol": limits.max_positions_per_symbol,
            "min_free_margin_pct": limits.min_free_margin_pct,
            "max_currency_notional_exposure_pct_warn": limits.max_currency_notional_exposure_pct_warn,
            "max_currency_notional_exposure_pct_block": limits.max_currency_notional_exposure_pct_block,
            "max_currency_open_risk_pct": limits.max_currency_open_risk_pct,
            "max_weekly_loss_pct": limits.max_weekly_loss_pct,
        },
        "sizing": sizing,
    }
