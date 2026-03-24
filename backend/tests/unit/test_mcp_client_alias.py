"""Tests for MCP client alias resolution and tool governance.

Verifies that:
- Aliased tool names are resolved to typed MCP specs (not generic {payload: object})
- Tool governance blocks disabled aliased tools correctly
- Direct MCP tool names still work
- has_tool() recognises both aliased and canonical names
"""

from app.services.agent_runtime.mcp_client import MCPClientAdapter, TOOL_ID_ALIASES


# ---------------------------------------------------------------------------
# Alias resolution in build_tool_specs
# ---------------------------------------------------------------------------

def test_build_tool_specs_returns_typed_schema_for_aliased_tool() -> None:
    adapter = MCPClientAdapter()
    specs = adapter.build_tool_specs(["market_regime_context"])

    assert len(specs) == 1
    spec = specs[0]
    # Spec published under the ALIAS name so the LLM uses it consistently
    assert spec["function"]["name"] == "market_regime_context"
    # Should have real typed parameters (not just a generic payload: object)
    props = spec["function"]["parameters"]["properties"]
    assert len(props) > 0, "Aliased tool spec must expose typed parameters"
    assert "payload" not in props, "Aliased tool must not fall back to generic payload schema"


def test_build_tool_specs_resolves_all_known_aliases() -> None:
    adapter = MCPClientAdapter()
    for alias in TOOL_ID_ALIASES:
        specs = adapter.build_tool_specs([alias])
        assert len(specs) == 1, f"No spec generated for alias {alias!r}"
        assert specs[0]["function"]["name"] == alias


def test_build_tool_specs_still_works_for_direct_mcp_names() -> None:
    adapter = MCPClientAdapter()
    specs = adapter.build_tool_specs(["indicator_bundle", "divergence_detector"])
    assert len(specs) == 2
    names = {s["function"]["name"] for s in specs}
    assert names == {"indicator_bundle", "divergence_detector"}


def test_build_tool_specs_deduplicates_same_tool() -> None:
    adapter = MCPClientAdapter()
    specs = adapter.build_tool_specs(["indicator_bundle", "indicator_bundle"])
    assert len(specs) == 1


# ---------------------------------------------------------------------------
# has_tool
# ---------------------------------------------------------------------------

def test_has_tool_recognises_canonical_name() -> None:
    adapter = MCPClientAdapter()
    assert adapter.has_tool("market_regime_detector") is True


def test_has_tool_recognises_aliased_name() -> None:
    adapter = MCPClientAdapter()
    assert adapter.has_tool("market_regime_context") is True


def test_has_tool_returns_false_for_unknown_name() -> None:
    adapter = MCPClientAdapter()
    assert adapter.has_tool("this_tool_does_not_exist") is False


# ---------------------------------------------------------------------------
# Tool governance — call_tool with enabled_tools
# ---------------------------------------------------------------------------

def test_call_tool_executes_when_alias_in_enabled_tools() -> None:
    adapter = MCPClientAdapter()
    result = adapter.call_tool(
        "market_regime_context",
        {"closes": [float(i) for i in range(1, 61)]},
        enabled_tools=["market_regime_context"],
    )
    assert result.status in {"ok", "error"}  # not disabled
    assert result.status != "disabled"


def test_call_tool_blocked_when_alias_not_in_enabled_tools() -> None:
    adapter = MCPClientAdapter()
    result = adapter.call_tool(
        "market_regime_context",
        {"closes": [float(i) for i in range(1, 61)]},
        enabled_tools=["indicator_bundle"],  # market_regime_context not listed
    )
    assert result.status == "disabled"


def test_call_tool_executes_when_canonical_name_in_enabled_tools() -> None:
    """Canonical MCP name in enabled_tools still allows the call."""
    adapter = MCPClientAdapter()
    result = adapter.call_tool(
        "market_regime_detector",
        {"closes": [float(i) for i in range(1, 61)]},
        enabled_tools=["market_regime_detector"],
    )
    assert result.status != "disabled"


def test_call_tool_blocked_when_empty_enabled_list() -> None:
    adapter = MCPClientAdapter()
    result = adapter.call_tool(
        "indicator_bundle",
        {"closes": [float(i) for i in range(1, 61)]},
        enabled_tools=[],
    )
    assert result.status == "disabled"


def test_call_tool_without_enabled_tools_guard_executes_freely() -> None:
    """When enabled_tools is None, no governance check is applied."""
    adapter = MCPClientAdapter()
    result = adapter.call_tool(
        "market_snapshot",
        {"symbol": "EURUSD", "last_price": 1.1},
    )
    # Should execute (or fail due to missing data) but not be disabled
    assert result.status != "disabled"
