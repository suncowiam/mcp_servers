"""Standalone MCP roundtrip smoke test.

Boots a stdio MCP client pointed at this server, calls list_csvs and
read_csv('properties'), and asserts the responses are reasonable.

Usage:
    uv run python scripts/smoke_test.py

Requires:
- GOOGLE_APPLICATION_CREDENTIALS env var pointing to a valid service account
- config/sheets.yaml with a real folder_id where the 5 Sheets exist
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        print("ERROR: GOOGLE_APPLICATION_CREDENTIALS env var is not set", file=sys.stderr)
        return 2

    server_path = Path(__file__).resolve().parent.parent / "src" / "pm_manage_csvs"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "pm_manage_csvs"],
        cwd=str(server_path.parent),
    )

    print("Starting MCP client...")
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        print("Session initialized.")

        print("\nCalling list_csvs...")
        result = await session.call_tool("list_csvs", {})
        csvs_text = _extract_all_text(result)
        print(f"  → {csvs_text!r}")
        import json as _json
        try:
            csvs_list = _json.loads(csvs_text)
        except (ValueError, TypeError):
            # FastMCP 1.29 emits one TextContent per list element; one-per-line
            csvs_list = [line.strip() for line in csvs_text.splitlines() if line.strip()]
        expected = {"properties", "tenants", "vendors", "maintenance", "events"}
        if set(csvs_list) != expected:
            print(f"  ✗ Mismatch: expected {sorted(expected)}", file=sys.stderr)
            return 1

        print("\nCalling get_schema('maintenance')...")
        result = await session.call_tool("get_schema", {"csv_name": "maintenance"})
        schema = _extract_all_text(result)
        print(f"  → {schema[:200]}...")

        print("\nCalling read_csv('properties')...")
        result = await session.call_tool("read_csv", {"csv_name": "properties"})
        rows = _extract_all_text(result)
        print(f"  → {rows[:200]}...")

    print("\n✓ Smoke test passed.")
    return 0


def _extract_all_text(tool_result) -> str:
    """Concatenate all text content blocks from an MCP tool result.

    FastMCP 1.29 renders list-typed tool returns as one TextContent per element.
    We join them with newlines so callers can json.loads the joined string.
    """
    parts = []
    for block in tool_result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


def _extract_text(tool_result) -> str:
    """Extract the first text content from an MCP tool result."""
    for block in tool_result.content:
        if hasattr(block, "text"):
            return block.text
    return str(tool_result)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
