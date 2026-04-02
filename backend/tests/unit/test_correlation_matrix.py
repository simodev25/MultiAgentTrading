"""Unit tests for correlation matrix."""

import numpy as np

from app.services.risk.correlation_matrix import CorrelationMatrix, compute_correlation_matrix


def test_perfect_correlation() -> None:
    """Two identical series -> correlation ~1.0."""
    prices = [100 + i * 0.5 for i in range(60)]
    result = compute_correlation_matrix(
        {"A": prices, "B": prices},
        lookback_days=5,
    )
    corr = result.get_correlation("A", "B")
    assert corr > 0.99


def test_inverse_correlation() -> None:
    """Two inversely related return series -> negative correlation."""
    # Generate prices where returns are inversely correlated
    np.random.seed(123)
    base_returns = np.random.randn(60) * 0.01
    prices_a = list(np.cumprod(1 + base_returns) * 100)
    prices_b = list(np.cumprod(1 - base_returns) * 100)  # Inverse returns
    result = compute_correlation_matrix(
        {"A": prices_a, "B": prices_b},
        lookback_days=5,
    )
    corr = result.get_correlation("A", "B")
    assert corr < -0.90


def test_cluster_detection() -> None:
    """Correlated symbols should cluster together."""
    # A and B perfectly correlated, C independent
    np.random.seed(42)
    base = np.cumsum(np.random.randn(60)) + 100
    noise = np.random.randn(60) * 0.01
    prices_a = list(base)
    prices_b = list(base + noise)  # Near-identical to A
    prices_c = list(np.cumsum(np.random.randn(60)) + 50)  # Independent

    result = compute_correlation_matrix(
        {"A": prices_a, "B": prices_b, "C": prices_c},
        lookback_days=5,
    )
    clusters = result.get_clusters(threshold=0.7)

    # A and B should be in same cluster
    ab_cluster = None
    for c in clusters:
        if "A" in c and "B" in c:
            ab_cluster = c
            break
    assert ab_cluster is not None, f"A and B should cluster together, got {clusters}"


def test_diversification_score_concentrated() -> None:
    """All correlated positions -> low diversification score."""
    matrix = CorrelationMatrix(
        symbols=["A", "B", "C"],
        matrix={"A": {"B": 0.9, "C": 0.85}, "B": {"C": 0.88}},
    )
    score = matrix.get_diversification_score(["A", "B", "C"])
    assert score < 0.2  # Low diversification


def test_diversification_score_diverse() -> None:
    """Uncorrelated positions -> high diversification score."""
    matrix = CorrelationMatrix(
        symbols=["A", "B", "C"],
        matrix={"A": {"B": 0.05, "C": -0.1}, "B": {"C": 0.02}},
    )
    score = matrix.get_diversification_score(["A", "B", "C"])
    assert score > 0.8  # High diversification


def test_missing_data_handling() -> None:
    """Symbol with insufficient data is excluded."""
    result = compute_correlation_matrix(
        {"A": [100, 101, 102], "B": list(range(60))},  # A too short
        lookback_days=5,
    )
    assert "A" not in result.symbols or result.data_quality.get("A", 1.0) == 0.0


def test_single_symbol() -> None:
    """Single symbol -> no matrix to compute."""
    result = compute_correlation_matrix({"A": list(range(60))}, lookback_days=5)
    assert len(result.symbols) <= 1


def test_self_correlation() -> None:
    """Self-correlation always returns 1.0."""
    matrix = CorrelationMatrix(symbols=["A"], matrix={})
    assert matrix.get_correlation("A", "A") == 1.0


def test_serialization_roundtrip() -> None:
    """to_dict/from_dict preserves data."""
    original = CorrelationMatrix(
        symbols=["A", "B"],
        matrix={"A": {"B": 0.75}},
        computed_at="2026-04-01T00:00:00Z",
        lookback_days=30,
        data_quality={"A": 1.0, "B": 0.9},
    )
    restored = CorrelationMatrix.from_dict(original.to_dict())
    assert restored.symbols == original.symbols
    assert restored.get_correlation("A", "B") == 0.75
