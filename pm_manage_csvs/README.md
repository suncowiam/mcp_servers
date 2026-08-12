# pm_manage_csvs

MCP server that exposes Google-Sheets-backed CSV storage for the pm Hermes agent. Read + write on five CSV types (properties, tenants, vendors, maintenance, events). No deletes.

## Quickstart

```bash
cd ~/.hermes/mcp_servers/pm_manage_csvs
uv sync                                  # install deps into .venv
uv sync --group dev                      # also install pytest, ruff, mypy

# Create your Drive folder and 5 Sheets, then:
$EDITOR config/sheets.yaml               # paste folder_id

# Verify it runs
.venv/bin/python -m pm_manage_csvs       # stdio; Ctrl-C to exit
```

## Tests

```bash
uv run pytest -v
uv run ruff check .
uv run mypy src/
make smoke                               # standalone MCP roundtrip
```

## Adding to Hermes

Already wired in `~/.hermes/profiles/pm/config.yaml` under `mcp_servers.pm_manage_csvs`. Restart pm gateway to activate:

```bash
pm gateway restart
```

## Architecture

See `design_summary.md` for full design.

## Layout

```
src/pm_manage_csvs/
├── __main__.py     # entry point
├── server.py       # FastMCP server + tool handlers
├── drive.py        # Drive API (folder listing, sheet lookup)
├── sheets.py       # Sheets API (read/append/update)
├── schema.py       # column definitions per CSV type
├── models.py       # pydantic row models
├── cache.py        # in-memory TTL cache
├── errors.py       # exception hierarchy
└── logging.py      # structured stderr logger
```
