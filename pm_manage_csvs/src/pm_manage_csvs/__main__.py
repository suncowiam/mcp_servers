"""Entry point: python -m pm_manage_csvs."""

from pm_manage_csvs.server import mcp


def main() -> None:
    """Run the MCP server with stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
