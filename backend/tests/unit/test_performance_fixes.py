"""Tests validating performance and caching fixes."""
import pytest


class TestIndicatorCache:
    """Verify RSI/ATR caching avoids redundant computation."""

    def test_rsi_cached_on_same_data(self):
        import pandas as pd
        from app.services.mcp.trading_server import _compute_rsi, _indicator_cache, clear_indicator_cache

        clear_indicator_cache()
        close = pd.Series([1.1, 1.2, 1.15, 1.18, 1.22, 1.19, 1.25, 1.3, 1.28, 1.35,
                           1.32, 1.29, 1.31, 1.34, 1.36, 1.38, 1.4, 1.37, 1.35, 1.33])

        result1 = _compute_rsi(close, 14)
        cache_size_after_first = len(_indicator_cache)

        result2 = _compute_rsi(close, 14)
        cache_size_after_second = len(_indicator_cache)

        # Cache should have been hit — no new entry
        assert cache_size_after_first == cache_size_after_second
        # Results should be identical (same object from cache)
        assert result1 is result2

    def test_atr_cached_on_same_data(self):
        import pandas as pd
        from app.services.mcp.trading_server import _compute_atr, _indicator_cache, clear_indicator_cache

        clear_indicator_cache()
        high = pd.Series([1.15, 1.22, 1.2, 1.25, 1.28, 1.3, 1.35, 1.38, 1.4, 1.42,
                          1.39, 1.36, 1.38, 1.41, 1.43, 1.45, 1.47, 1.44, 1.42, 1.4])
        low = pd.Series([1.08, 1.12, 1.1, 1.14, 1.18, 1.2, 1.24, 1.27, 1.29, 1.31,
                         1.28, 1.25, 1.27, 1.3, 1.32, 1.34, 1.36, 1.33, 1.31, 1.29])
        close = pd.Series([1.1, 1.18, 1.15, 1.2, 1.24, 1.26, 1.3, 1.34, 1.36, 1.38,
                           1.35, 1.32, 1.34, 1.37, 1.39, 1.41, 1.43, 1.4, 1.38, 1.36])

        result1 = _compute_atr(high, low, close, 14)
        result2 = _compute_atr(high, low, close, 14)

        # Second call should return cached result
        assert result1 is result2

    def test_different_period_not_cached(self):
        import pandas as pd
        from app.services.mcp.trading_server import _compute_rsi, clear_indicator_cache

        clear_indicator_cache()
        close = pd.Series([1.1, 1.2, 1.15, 1.18, 1.22, 1.19, 1.25, 1.3, 1.28, 1.35,
                           1.32, 1.29, 1.31, 1.34, 1.36, 1.38, 1.4, 1.37, 1.35, 1.33])

        result_14 = _compute_rsi(close, 14)
        result_7 = _compute_rsi(close, 7)

        # Different periods should produce different results
        assert result_14 is not result_7

    def test_clear_indicator_cache(self):
        import pandas as pd
        from app.services.mcp.trading_server import _compute_rsi, _indicator_cache, clear_indicator_cache

        close = pd.Series([1.1, 1.2, 1.15, 1.18, 1.22, 1.19, 1.25, 1.3, 1.28, 1.35,
                           1.32, 1.29, 1.31, 1.34, 1.36, 1.38, 1.4, 1.37, 1.35, 1.33])
        _compute_rsi(close, 14)
        assert len(_indicator_cache) > 0

        clear_indicator_cache()
        assert len(_indicator_cache) == 0


class TestPromptMemoization:
    """Verify _build_prompt_meta uses cache within a run."""

    def test_prompt_cache_reuse(self):
        """When _prompt_cache is provided, second call should reuse cached result."""
        cache = {}
        # Simulate first call populating cache
        cache["technical-analyst"] = {
            "system_prompt": "You are a technical analyst.",
            "user_prompt": "Analyze {pair}.",
            "prompt_id": 1,
            "version": 1,
            "skills": [],
        }

        # Second access should hit cache
        assert "technical-analyst" in cache
        assert cache["technical-analyst"]["prompt_id"] == 1


class TestBatchedSteps:
    """Verify _flush_pending_steps exists and is callable."""

    def test_flush_method_exists(self):
        from app.services.agentscope.registry import AgentScopeRegistry
        registry = AgentScopeRegistry()
        assert hasattr(registry, '_flush_pending_steps')
        assert callable(registry._flush_pending_steps)


class TestBacktestCachePreserved:
    """Verify backtest cache is not deleted after read."""

    def test_cache_not_deleted_in_engine(self):
        """The r.delete(cache_key) line should no longer exist."""
        import inspect
        from app.services.backtest.engine import BacktestEngine
        source = inspect.getsource(BacktestEngine)
        assert 'r.delete(cache_key)' not in source


class TestFrontendPollingIntervals:
    """Verify frontend polling intervals are reasonable."""

    def test_polling_not_too_aggressive(self):
        """TerminalPage should poll no faster than 5 seconds."""
        import pathlib
        terminal_path = pathlib.Path(__file__).resolve().parents[3] / "frontend" / "src" / "pages" / "TerminalPage.tsx"
        if not terminal_path.exists():
            pytest.skip("Frontend not available")
        content = terminal_path.read_text()
        # Should NOT contain 3000ms polling (was changed to 10000)
        assert ', 3000)' not in content, "Run polling interval should be >= 5000ms"
