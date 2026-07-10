#!/usr/bin/env python3
"""
Migrate Secret Santa legacy archives to unified format and create test archive 3000.

Run from repo root:
    python scripts/migrate_ss_archives.py
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.secret_santa_assignments import make_assignments  # noqa: E402
from cogs.secret_santa_storage import (  # noqa: E402
    ARCHIVE_DIR,
    TEST_ARCHIVE_YEAR,
    count_event_participants,
    load_all_archives,
    load_json,
    normalize_archive,
    save_json,
)


def migrate_existing_archives() -> None:
    for archive_file in sorted(ARCHIVE_DIR.glob("[0-9]*.json")):
        if "backups" in archive_file.parts or "_backup_" in archive_file.name.lower():
            continue
        year_str = archive_file.stem
        if not year_str.isdigit() or len(year_str) != 4:
            continue
        year = int(year_str)
        if year == TEST_ARCHIVE_YEAR:
            continue

        raw = load_json(archive_file)
        if not raw:
            print(f"  skip {archive_file.name} (empty)")
            continue

        if "event" in raw and "assignments" not in raw:
            print(f"  ok   {archive_file.name} (already unified)")
            continue

        normalized = normalize_archive(raw, year)
        if raw.get("archived_at"):
            normalized["archived_at"] = raw["archived_at"]
        elif "archived_at" not in normalized:
            normalized["archived_at"] = time.time()
        if raw.get("timestamp"):
            normalized["timestamp"] = raw["timestamp"]
        else:
            normalized["timestamp"] = dt.datetime.now().isoformat()

        save_json(archive_file, normalized)
        n = count_event_participants(normalized["event"])
        print(f"  migrated {archive_file.name} → {n} participants")


def build_test_archive_3000() -> None:
    archives = load_all_archives()
    participants: dict[str, str] = {}
    for year, data in archives.items():
        if year == TEST_ARCHIVE_YEAR:
            continue
        event = data.get("event") or {}
        for uid, name in (event.get("participants") or {}).items():
            participants[str(uid)] = name or f"User {uid}"

    if len(participants) < 2:
        raise SystemExit("Need at least 2 historical participants to build test archive")

    participant_ids = sorted(int(uid) for uid in participants.keys())
    assignments_int = make_assignments(participant_ids, {}, logger=None)
    assignments = {str(g): str(r) for g, r in assignments_int.items()}

    archive_data = {
        "year": TEST_ARCHIVE_YEAR,
        "test_archive": True,
        "description": "Synthetic archive — all historical participants (excluded from shuffle history)",
        "archived_at": time.time(),
        "timestamp": dt.datetime.now().isoformat(),
        "event": {
            "active": False,
            "participants": participants,
            "assignments": assignments,
            "gift_submissions": {},
        },
    }

    out_path = ARCHIVE_DIR / f"{TEST_ARCHIVE_YEAR}.json"
    save_json(out_path, archive_data)
    print(
        f"  created {out_path.name} → {count_event_participants(archive_data['event'])} participants"
    )


def main() -> None:
    print("Migrating Secret Santa archives…")
    migrate_existing_archives()
    print("Building test archive 3000…")
    build_test_archive_3000()
    print("\nFinal counts (/ss history overview):")
    archives = load_all_archives()
    for year in sorted(archives.keys(), reverse=True):
        event = archives[year]["event"]
        n = count_event_participants(event)
        tag = " (test)" if year == TEST_ARCHIVE_YEAR else ""
        print(f"  Secret Santa {year}: {n} participants{tag}")


if __name__ == "__main__":
    main()
