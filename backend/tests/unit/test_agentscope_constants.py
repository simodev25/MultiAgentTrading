from app.services.agentscope.constants import (
    CONSERVATIVE, BALANCED, PERMISSIVE, DecisionGatingPolicy,
    TIMEFRAME_ORDER, MAX_USEFUL_TF, TREND_WEIGHT, MACD_WEIGHT,
    SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER, FIAT_ASSETS, CRYPTO_ASSETS,
    COMMODITY_ASSETS, higher_timeframes, EMA_WEIGHT, RSI_WEIGHT,
    CHANGE_WEIGHT, PATTERN_WEIGHT, DIVERGENCE_WEIGHT, MULTI_TF_WEIGHT, LEVEL_WEIGHT,
)

def test_policy_thresholds_ordered():
    assert PERMISSIVE.min_combined_score < BALANCED.min_combined_score < CONSERVATIVE.min_combined_score
    assert PERMISSIVE.min_confidence < BALANCED.min_confidence < CONSERVATIVE.min_confidence

def test_conservative_blocks_single_source_override():
    assert CONSERVATIVE.allow_technical_single_source_override is False
    assert BALANCED.allow_technical_single_source_override is True
    assert PERMISSIVE.allow_technical_single_source_override is True

def test_all_modes_block_major_contradiction():
    assert CONSERVATIVE.block_major_contradiction is True
    assert BALANCED.block_major_contradiction is True
    assert PERMISSIVE.block_major_contradiction is True

def test_scoring_weights_sum_near_one():
    total = (TREND_WEIGHT + EMA_WEIGHT + RSI_WEIGHT + MACD_WEIGHT +
             CHANGE_WEIGHT + PATTERN_WEIGHT + DIVERGENCE_WEIGHT +
             MULTI_TF_WEIGHT + LEVEL_WEIGHT)
    assert 0.95 <= total <= 1.15

def test_timeframe_order():
    assert TIMEFRAME_ORDER[0] == "M1"
    assert TIMEFRAME_ORDER[-1] == "MN"
    assert MAX_USEFUL_TF == "D1"

def test_higher_timeframes():
    assert higher_timeframes("M5") == ["M15", "M30"]
    assert higher_timeframes("H4") == ["D1"]
    assert higher_timeframes("D1") == []
    assert higher_timeframes("MN") == []

def test_asset_lists_non_empty():
    assert len(FIAT_ASSETS) == 8
    assert "USD" in FIAT_ASSETS
    assert len(CRYPTO_ASSETS) == 14
    assert "BTC" in CRYPTO_ASSETS
    assert len(COMMODITY_ASSETS) == 2
