"""Correlation matrix — full pairwise correlation between traded instruments.

Computed daily, stored in Redis (TTL 24h).
Uses numpy for vectorized correlation. No scipy dependency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger(__name__)

REDIS_KEY = "risk:correlation_matrix"
REDIS_TTL = 86400  # 24 hours


@dataclass
class CorrelationMatrix:
    symbols: list[str] = field(default_factory=list)
    matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    computed_at: str = ""
    lookback_days: int = 30
    data_quality: dict[str, float] = field(default_factory=dict)

    def get_correlation(self, a: str, b: str) -> float:
        """Get correlation between two symbols. Returns 0.0 if unknown."""
        if a == b:
            return 1.0
        return self.matrix.get(a, {}).get(b, self.matrix.get(b, {}).get(a, 0.0))

    def get_clusters(self, threshold: float = 0.7) -> list[list[str]]:
        """Identify clusters of highly correlated symbols (abs(corr) >= threshold).

        Uses simple graph-based clustering: symbols connected by high correlation
        are grouped together via BFS.
        """
        if not self.symbols:
            return []

        # Build adjacency list
        adj: dict[str, set[str]] = {s: set() for s in self.symbols}
        for i, a in enumerate(self.symbols):
            for b in self.symbols[i + 1:]:
                if abs(self.get_correlation(a, b)) >= threshold:
                    adj[a].add(b)
                    adj[b].add(a)

        # BFS to find connected components
        visited: set[str] = set()
        clusters: list[list[str]] = []

        for symbol in self.symbols:
            if symbol in visited:
                continue
            cluster: list[str] = []
            queue = [symbol]
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                cluster.append(node)
                for neighbor in adj[node]:
                    if neighbor not in visited:
                        queue.append(neighbor)
            clusters.append(sorted(cluster))

        return [c for c in clusters if len(c) > 0]

    def get_diversification_score(self, positions: list[str]) -> float:
        """Compute diversification score for a set of position symbols.

        0.0 = all positions perfectly correlated
        1.0 = all positions perfectly uncorrelated
        """
        unique = list(set(positions))
        if len(unique) < 2:
            return 1.0

        total = 0.0
        count = 0
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                corr = self.get_correlation(unique[i], unique[j])
                total += (1.0 - abs(corr))
                count += 1

        return round(total / count, 4) if count > 0 else 1.0

    def to_dict(self) -> dict:
        """Serialize for Redis storage."""
        return {
            "symbols": self.symbols,
            "matrix": self.matrix,
            "computed_at": self.computed_at,
            "lookback_days": self.lookback_days,
            "data_quality": self.data_quality,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CorrelationMatrix:
        """Deserialize from Redis."""
        return cls(
            symbols=data.get("symbols", []),
            matrix=data.get("matrix", {}),
            computed_at=data.get("computed_at", ""),
            lookback_days=data.get("lookback_days", 30),
            data_quality=data.get("data_quality", {}),
        )


def compute_correlation_matrix(
    close_prices: dict[str, list[float]],
    lookback_days: int = 30,
) -> CorrelationMatrix:
    """Compute full pairwise correlation matrix from close prices.

    Args:
        close_prices: Dict mapping symbol -> list of close prices (H4 bars).
                      All lists should cover the same time period.
        lookback_days: Window for correlation (in trading days, ~6 bars/day for H4).

    Returns:
        CorrelationMatrix with pairwise Pearson correlations on log returns.
    """
    symbols = sorted(close_prices.keys())
    if len(symbols) < 2:
        return CorrelationMatrix(
            symbols=symbols,
            computed_at=datetime.now(timezone.utc).isoformat(),
            lookback_days=lookback_days,
        )

    # Convert to log returns
    returns: dict[str, np.ndarray] = {}
    data_quality: dict[str, float] = {}
    min_bars = lookback_days * 6  # ~6 H4 bars per day

    for sym in symbols:
        prices = np.array(close_prices[sym], dtype=float)
        if len(prices) < 10:
            data_quality[sym] = 0.0
            continue

        # Use last min_bars if available
        if len(prices) > min_bars:
            prices = prices[-min_bars:]

        # Log returns
        with np.errstate(divide="ignore", invalid="ignore"):
            ret = np.diff(np.log(prices))
            ret = ret[np.isfinite(ret)]

        if len(ret) < 10:
            data_quality[sym] = 0.0
            continue

        returns[sym] = ret
        data_quality[sym] = round(len(ret) / min_bars, 2)

    # Filter symbols with sufficient data
    valid_symbols = [s for s in symbols if s in returns]

    # Compute pairwise correlations
    matrix: dict[str, dict[str, float]] = {}

    for i, sym_a in enumerate(valid_symbols):
        matrix[sym_a] = {}
        for sym_b in valid_symbols[i + 1:]:
            ret_a = returns[sym_a]
            ret_b = returns[sym_b]

            # Align lengths
            common_len = min(len(ret_a), len(ret_b))
            if common_len < 10:
                matrix[sym_a][sym_b] = 0.0
                continue

            a = ret_a[-common_len:]
            b = ret_b[-common_len:]

            # Pearson correlation via numpy
            corr_matrix = np.corrcoef(a, b)
            corr = float(corr_matrix[0, 1]) if np.isfinite(corr_matrix[0, 1]) else 0.0
            matrix[sym_a][sym_b] = round(corr, 4)

    return CorrelationMatrix(
        symbols=valid_symbols,
        matrix=matrix,
        computed_at=datetime.now(timezone.utc).isoformat(),
        lookback_days=lookback_days,
        data_quality=data_quality,
    )


def save_to_redis(cm: CorrelationMatrix) -> None:
    """Store correlation matrix in Redis with 24h TTL."""
    try:
        import json
        import redis
        from app.core.config import get_settings
        r = redis.from_url(get_settings().redis_url)
        r.setex(REDIS_KEY, REDIS_TTL, json.dumps(cm.to_dict()))
        logger.info("Correlation matrix saved to Redis (%d symbols)", len(cm.symbols))
    except Exception as exc:
        logger.warning("Failed to save correlation matrix to Redis: %s", exc)


def load_from_redis() -> CorrelationMatrix | None:
    """Load correlation matrix from Redis. Returns None if not found."""
    try:
        import json
        import redis
        from app.core.config import get_settings
        r = redis.from_url(get_settings().redis_url)
        raw = r.get(REDIS_KEY)
        if raw is None:
            return None
        return CorrelationMatrix.from_dict(json.loads(raw))
    except Exception as exc:
        logger.debug("Failed to load correlation matrix from Redis: %s", exc)
        return None
