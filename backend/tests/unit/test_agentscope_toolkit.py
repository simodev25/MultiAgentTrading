import pytest
from app.services.agentscope.toolkit import AGENT_TOOL_MAP, build_toolkit
from app.services.mcp.client import get_mcp_client


def test_agent_tool_map_has_all_agents():
    expected = {
        "technical-analyst", "news-analyst", "market-context-analyst",
        "bullish-researcher", "bearish-researcher", "trader-agent",
        "risk-manager", "execution-manager",
    }
    assert set(AGENT_TOOL_MAP.keys()) == expected


def test_agent_tool_map_tools_exist_in_mcp():
    client = get_mcp_client()
    all_tools = client.list_tools()
    for agent_name, tool_ids in AGENT_TOOL_MAP.items():
        for tool_id in tool_ids:
            assert tool_id in all_tools, f"{tool_id} not found in MCP for {agent_name}"


@pytest.mark.asyncio
async def test_build_toolkit_returns_toolkit():
    toolkit = await build_toolkit("technical-analyst")
    schemas = toolkit.get_json_schemas()
    assert len(schemas) > 0
    tool_names = {s["function"]["name"] for s in schemas}
    assert "indicator_bundle" in tool_names


@pytest.mark.asyncio
async def test_build_toolkit_unknown_agent_empty():
    toolkit = await build_toolkit("unknown-agent")
    schemas = toolkit.get_json_schemas()
    assert len(schemas) == 0
