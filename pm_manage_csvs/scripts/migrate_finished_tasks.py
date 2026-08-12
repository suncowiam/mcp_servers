"""One-shot: migrate workspace/TASKS_FINISHED.md → maintenance Sheet.

Parses completed tasks from the MD file and appends each as a maintenance row
with status="done" and completed_at set to the closure date.

The source file uses different formats across sections:
- "Property | Completed | Issue/Task" (Chris/Sharon sections)
- "Property | Closed | Issue/Task" (Tuan/Closures sections)

All formats are 3-column tables; we just need to recognize the column name.

Run: GOOGLE_APPLICATION_CREDENTIALS=... uv run python scripts/migrate_finished_tasks.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

WORKSPACE = Path("/home/suncowiam/.hermes/profiles/pm/workspace")


def parse_date(s: str) -> str | None:
    """Convert 'Apr 18' to '2026-04-18'."""
    s = s.strip()
    if not s or s in ("—", "-"):
        return None
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2})", s)
    if not m or m.group(1) not in MONTHS:
        return None
    return f"2026-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def strip_md(s: str) -> str:
    """Remove strikethrough and leading pipe chars."""
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"^\|+\s*", "", s)
    return s.strip()


def main() -> int:
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path:
        print("ERROR: GOOGLE_APPLICATION_CREDENTIALS not set", file=sys.stderr)
        return 2

    sys.path.insert(0, "/home/suncowiam/.hermes/mcp_servers/pm_manage_csvs/src")

    md_path = WORKSPACE / "TASKS_FINISHED.md"
    if not md_path.exists():
        print(f"ERROR: {md_path} not found", file=sys.stderr)
        return 2

    text = md_path.read_text()

    # Determine current owner from headers — supports sections like:
    # "## Chris Krull", "### Sharon Brown", "## Closures — 08-04-26"
    # then "### Chris Krull" under it (inherit Closures' implicit context)
    tasks = []
    owner = "chris"  # default

    for line in text.splitlines():
        # Owner headers
        if re.match(r"^#{2,4}\s+Tuan", line):
            owner = "tuan"
            continue
        if re.match(r"^#{2,4}\s+Chris", line):
            owner = "chris"
            continue
        if re.match(r"^#{2,4}\s+Sharon Brown", line):
            owner = "sharon"
            continue

        # "Closures -- DD-MM-YY" header (em/en dash both accepted)
        m_closers = re.match(r"^#{2,4}\s+Closures\s*[—–-]\s*(\d{2}-\d{2}-\d{2})", line)  # noqa: RUF001
        if m_closers:
            owner = "chris"  # reset; sub-headers below will refine
            continue

        if not line.startswith("|"):
            continue
        if "---" in line or "Issue" in line or "Closed" in line:
            continue

        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 3:
            continue

        property_cell, date_cell, issue_cell = cells[0], cells[1], cells[2]
        property_clean = strip_md(property_cell)
        issue_clean = strip_md(issue_cell)
        if not property_clean or not issue_clean:
            continue
        if property_clean in ("Property",):
            continue

        completed_at = parse_date(date_cell)
        tasks.append({
            "property": property_clean,
            "owner": owner,
            "issue": issue_clean,
            "status": "done",
            "completed_at": completed_at or "",
        })

    print(f"Parsed {len(tasks)} completed tasks from {md_path.name}")
    for t in tasks[:3]:
        print(f"  [{t['owner']}] {t['property']} ({t['completed_at']}): {t['issue'][:50]}...")
    print(f"  ... and {len(tasks) - 3} more")

    if "--dry-run" in sys.argv:
        return 0

    from pm_manage_csvs import server

    # Idempotency: skip rows that already exist (matched by issue text on done rows)
    existing = server.read_csv_rows.__wrapped__("maintenance", filters={"status": "done"})
    existing_issues = {r.get("issue", "") for r in existing}
    to_add = [t for t in tasks if t["issue"] not in existing_issues]

    print(f"\nAfter dedup against existing done rows: {len(to_add)} to append")
    if not to_add:
        print("Nothing new to add.")
        return 0

    result = server.append_rows.__wrapped__("maintenance", to_add)
    print(f"Appended {len(result)} rows → indices {result[:3]}...{result[-3:] if len(result) > 3 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())