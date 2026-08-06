"""Load Secret Santa archive JSON for the local history simulator (not the Discord bot)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIR = REPO_ROOT / "cogs" / "archive"
SIM_YEAR = 3000


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _participants_from_legacy(assignments: list) -> dict[str, str]:
    people: dict[str, str] = {}
    for row in assignments:
        if not isinstance(row, dict):
            continue
        if row.get("giver_ids"):
            for gid, name in zip(row.get("giver_ids") or [], row.get("giver_names") or []):
                if gid:
                    people[str(gid)] = name or "Unknown"
            continue
        gid = str(row.get("giver_id") or "")
        rid = str(row.get("receiver_id") or "")
        if gid:
            people[gid] = row.get("giver_name") or "Unknown"
        if rid:
            people[rid] = row.get("receiver_name") or "Unknown"
    return people


def _assignments_from_legacy(assignments: list) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row in assignments:
        if not isinstance(row, dict) or row.get("giver_ids"):
            continue
        gid = str(row.get("giver_id") or "")
        rid = str(row.get("receiver_id") or "")
        if gid and rid:
            pairs[gid] = rid
    return pairs


def _normalize_year_file(data: dict, year: int) -> dict[str, Any]:
    if "event" in data and isinstance(data["event"], dict):
        event = data["event"]
        participants = event.get("participants") or {}
        assignments = event.get("assignments") or {}
        gifts = event.get("gift_submissions") or {}
    else:
        rows = data.get("assignments") or []
        participants = _participants_from_legacy(rows)
        assignments = _assignments_from_legacy(rows)
        gifts = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("giver_ids"):
                continue
            gid = str(row.get("giver_id") or "")
            gift = row.get("gift")
            if gid and isinstance(gift, str) and gift.strip():
                gifts[gid] = {"gift": gift}

    ids = set(assignments.keys()) | set(assignments.values())
    participant_count = len(ids) if ids else len(participants)
    gift_count = sum(
        1 for gid in assignments if isinstance((gifts.get(str(gid)) or {}).get("gift"), str)
        and (gifts.get(str(gid)) or {}).get("gift", "").strip()
    )

    return {
        "year": year,
        "participant_count": participant_count,
        "assignment_count": len(assignments),
        "gift_count": gift_count,
        "participants": participants,
        "assignments": assignments,
        "gifts": gifts,
        "simulated": False,
    }


def load_history(include_sim_year: bool = True) -> list[dict[str, Any]]:
    years: list[dict[str, Any]] = []
    all_people: dict[str, str] = {}

    for path in sorted(ARCHIVE_DIR.glob("[0-9][0-9][0-9][0-9].json")):
        if "backups" in path.parts or "_backup_" in path.name.lower():
            continue
        year = int(path.stem)
        data = _load_json(path)
        if not data:
            continue
        entry = _normalize_year_file(data, year)
        years.append(entry)
        all_people.update(entry["participants"])

    years.sort(key=lambda e: e["year"], reverse=True)

    if include_sim_year and len(all_people) >= 2:
        years.insert(0, {
            "year": SIM_YEAR,
            "participant_count": len(all_people),
            "assignment_count": len(all_people),
            "gift_count": 0,
            "participants": all_people,
            "assignments": {},
            "gifts": {},
            "simulated": True,
            "note": "Local simulation only — not stored in cogs/archive and not used by the bot",
        })

    return years


def load_year_detail(year: int) -> dict[str, Any] | None:
    if year == SIM_YEAR:
        history = load_history(include_sim_year=True)
        return next((y for y in history if y["year"] == SIM_YEAR), None)

    path = ARCHIVE_DIR / f"{year}.json"
    if not path.exists():
        return None
    return _normalize_year_file(_load_json(path), year)
