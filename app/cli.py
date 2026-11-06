"""Project CLI: `python -m app.cli <command>`."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings


def _sqlite_file_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    path = url.removeprefix("sqlite:///")
    return Path(urllib.parse.unquote(path))


def backup() -> int:
    """Consistent snapshot via SQLite `VACUUM INTO` (DATABASE_DESIGN §6)."""
    settings.ensure_dirs()
    db_file = _sqlite_file_from_url(settings.database_url)
    if db_file is None:
        print("Backup supports the SQLite database only (DATABASE_URL is not sqlite).")
        return 1
    if not db_file.exists():
        print(f"No database yet at {db_file} — nothing to back up.")
        return 0
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = settings.backups_dir / f"prospectly-{stamp}.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("VACUUM INTO ?", (str(target),))
    print(f"Backup written: {target}")
    return 0


def export_openapi() -> int:
    """Write the OpenAPI contract to docs/openapi.json (API_DESIGN §5)."""
    from app.main import app

    docs_dir = settings.data_dir.parent / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(app.openapi(), indent=2, ensure_ascii=False)
    (docs_dir / "openapi.json").write_text(payload, encoding="utf-8")
    print(f"OpenAPI written: {docs_dir / 'openapi.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Prospectly maintenance commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup", help="Snapshot the SQLite database into backups/")
    sub.add_parser("export-openapi", help="Write docs/openapi.json from the live app")
    args = parser.parse_args(argv)

    if args.command == "backup":
        return backup()
    if args.command == "export-openapi":
        return export_openapi()
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
