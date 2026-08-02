"""Conformance tests for MCP protocol revision 2026-07-28.

These drive a real in-process client against the server, so they exercise the
dispatch path where cache hints and result metadata are applied. Reading the
handler functions directly would bypass it, and the cache-hint assertions would
pass while checking nothing.
"""

from __future__ import annotations

import anyio
import pytest
from mcp.client.client import Client

import server

PROTOCOL_VERSION = "2026-07-28"


def _run(body):
    async def main():
        async with Client(server.server) as client:
            value = body(client)
            return await value if hasattr(value, "__await__") else value
    return anyio.run(main)


def _wire(body):
    return _run(body).model_dump(by_alias=True, exclude_none=True)


def test_negotiates_2026_07_28():
    """Before the migration this server hand-rolled a JSON-RPC loop and
    answered `initialize` with protocolVersion 2024-11-05."""
    assert _run(lambda c: c.protocol_version) == PROTOCOL_VERSION


def test_tools_list_carries_cache_hints():
    """SEP-2549. Asserted on the serialized wire form, since the snake_case
    attributes would still pass if the camelCase aliases regressed."""
    wire = _wire(lambda c: c.list_tools())
    assert wire["ttlMs"] == 300_000
    assert wire["cacheScope"] == "public"


def test_results_carry_result_type():
    assert _wire(lambda c: c.list_tools())["resultType"] == "complete"


def test_server_identifies_itself_in_result_meta():
    info = _wire(lambda c: c.list_tools())["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert info["name"] == "skillmatch-mcp"
    assert info["version"] == server.SERVER_VERSION


def test_server_discover_advertises_supported_versions():
    """Servers MUST implement server/discover."""
    wire = _wire(lambda c: c.session.discover())
    assert PROTOCOL_VERSION in wire["supportedVersions"]
    assert wire["ttlMs"] == 300_000
    assert wire["cacheScope"] == "public"


def test_tool_order_is_deterministic_and_matches_TOOLS():
    """Servers SHOULD return tools in a deterministic order so client-side and
    LLM prompt caches keep hitting. The order is TOOLS' declaration order."""
    first = [t["name"] for t in _wire(lambda c: c.list_tools())["tools"]]
    second = [t["name"] for t in _wire(lambda c: c.list_tools())["tools"]]

    assert first == [t["name"] for t in server.TOOLS]
    assert first == second


def test_published_schemas_are_unchanged_by_the_migration():
    """This package is on PyPI, so an existing install's tool schemas must not
    drift. TOOLS is passed to the SDK verbatim rather than being re-derived
    from function signatures; this guards that it stays that way."""
    served = {t["name"]: t for t in _wire(lambda c: c.list_tools())["tools"]}
    declared = {t["name"]: t for t in server.TOOLS}

    assert set(served) == set(declared)
    for name, decl in declared.items():
        assert served[name]["inputSchema"] == decl["inputSchema"], f"schema drift in {name}"
        assert served[name]["description"] == decl["description"], f"description drift in {name}"


def test_every_declared_tool_has_a_handler():
    """A tool in TOOLS with no HANDLERS entry is advertised but uncallable."""
    assert set(t["name"] for t in server.TOOLS) - set(server.HANDLERS) == set()


@pytest.mark.parametrize("method", ["initialize"])
def test_handshake_methods_are_gone(method):
    """The 2026-07-28 revision has no handshake. `initialize` is reserved by the
    SDK runner and is not something this server answers any more."""
    assert method not in server.server._request_handlers
