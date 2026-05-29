"""
Secret Santa Storage Module – File I/O and State Management

This module is the single source of truth for Secret Santa persistence. All state,
archives, and metadata flow through these functions.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW IT ALL WORKS TOGETHER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  SecretSanta_cog (in-memory)  ←→  load_state_with_fallback / save_state
              ↑                                    ↑
              │                                    │
              └────────────────────────────────────┘
                         STATE_FILE
                    (secret_santa_state.json)
                    + .backup fallback

  Past years (read-only)  ←──  load_all_archives  ←──  archive/*.json
  Assignment history      ←──  load_history_from_archives (assignments module)
  New year write         ──→  archive_event     ──→  archive/{year}.json

RESPONSIBILITIES:
  • JSON load/save with atomic writes (crash-safe)
  • Multi-layer state fallback (main → .backup → defaults)
  • Archive loading with legacy format conversion
  • Cross-platform paths (Windows/Linux)
"""

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

# ─── Paths (relative to cogs directory) ───────────────────────────────────────
ROOT: Path = Path(__file__).parent  # cogs/
STATE_FILE: Path = ROOT / "secret_santa_state.json"  # Live event + current_year
ARCHIVE_DIR: Path = ROOT / "archive"  # Past years: 2021.json, 2022.json, ...
BACKUPS_DIR: Path = ARCHIVE_DIR / "backups"  # Indestructible backups (never auto-deleted)

ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

# Synthetic test archive (all historical participants); excluded from assignment history by default
TEST_ARCHIVE_YEAR = 3000

# Max file size to prevent DoS from huge/corrupt files (10MB is plenty for state/archives)
LOAD_JSON_MAX_BYTES = 10 * 1024 * 1024


def is_valid_archive_year(year: int, archived_years: Optional[Any] = None) -> bool:
    """
    Return True if ``year`` is valid for history/edit commands.

    Allows any year that already has an archive file (including test year 3000),
    plus the normal operational range (2020 … current calendar year + 1).
    """
    if not isinstance(year, int):
        return False
    if archived_years is not None:
        try:
            if year in archived_years:
                return True
        except TypeError:
            pass
    today_year = dt.date.today().year
    return 2020 <= year <= today_year + 1


def count_event_participants(event_data: dict) -> int:
    """
    Participant count for history embeds — prefer assignment pairs over raw dict size.

    Uses unique giver/receiver IDs from ``assignments`` when present; falls back to
    ``participants`` dict length.
    """
    if not isinstance(event_data, dict):
        return 0
    assignments = event_data.get("assignments")
    if isinstance(assignments, dict) and assignments:
        ids = {str(k) for k in assignments.keys()} | {str(v) for v in assignments.values()}
        return len(ids)
    participants = event_data.get("participants")
    if isinstance(participants, dict):
        return len(participants)
    return 0


def normalize_archive(data: dict, year: int, logger=None) -> dict:
    """
    Normalize legacy or unified archive JSON to the canonical on-disk structure.

    Handles:
    - Unified format (``event`` key) — returned with missing keys filled in
    - Legacy ``assignments`` list — converted to ``event`` with participants map
    - Special multi-giver entries (``giver_ids``) — stored under ``event.special_gifts``
    """
    if not isinstance(data, dict):
        data = {}

    if "event" in data and isinstance(data.get("event"), dict):
        event = dict(data["event"])
        if "participants" not in event or not isinstance(event.get("participants"), dict):
            event["participants"] = event.get("participants") if isinstance(event.get("participants"), dict) else {}
        if "assignments" not in event or not isinstance(event.get("assignments"), dict):
            event["assignments"] = event.get("assignments") if isinstance(event.get("assignments"), dict) else {}
        if "gift_submissions" not in event or not isinstance(event.get("gift_submissions"), dict):
            event["gift_submissions"] = event.get("gift_submissions") if isinstance(event.get("gift_submissions"), dict) else {}
        if "special_gifts" not in event:
            event["special_gifts"] = event.get("special_gifts") if isinstance(event.get("special_gifts"), list) else []
        result = dict(data)
        result["year"] = year
        result["event"] = event
        return result

    participants: Dict[str, str] = {}
    gifts: Dict[str, dict] = {}
    assignments_map: Dict[str, str] = {}
    special_gifts: list = []

    for assignment in data.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue

        if assignment.get("giver_ids"):
            special_gifts.append({
                "giver_ids": assignment.get("giver_ids") or [],
                "giver_names": assignment.get("giver_names") or [],
                "receiver_name": assignment.get("receiver_name"),
                "gift": assignment.get("gift"),
                "special_note": assignment.get("special_note"),
            })
            for gid, gname in zip(
                assignment.get("giver_ids") or [],
                assignment.get("giver_names") or [],
            ):
                if gid:
                    participants[str(gid)] = gname or "Unknown"
            continue

        giver_id = str(assignment.get("giver_id") or "")
        giver_name = assignment.get("giver_name", "Unknown")
        receiver_id = str(assignment.get("receiver_id") or "")
        receiver_name = assignment.get("receiver_name", "Unknown")
        gift = assignment.get("gift")

        if giver_id:
            participants[giver_id] = giver_name
        if receiver_id:
            participants[receiver_id] = receiver_name
        if giver_id and receiver_id:
            assignments_map[giver_id] = receiver_id
        if giver_id and isinstance(gift, str) and gift.strip():
            gifts[giver_id] = {
                "gift": gift,
                "receiver_name": receiver_name,
                "receiver_id": receiver_id,
            }

    event = {
        "active": False,
        "participants": participants,
        "assignments": assignments_map,
        "gift_submissions": gifts,
    }
    if special_gifts:
        event["special_gifts"] = special_gifts

    result = {
        "year": year,
        "event": event,
    }
    if data.get("archived_at") is not None:
        result["archived_at"] = data["archived_at"]
    if data.get("timestamp"):
        result["timestamp"] = data["timestamp"]
    if data.get("statistics"):
        result["statistics"] = data["statistics"]
    return result


def load_json(path: Path, default: Any = None) -> Any:
    """
    Load JSON from disk with graceful error handling.

    Returns the parsed content on success. On failure (missing file, invalid JSON,
    encoding error, file too large), returns the default. Uses ``default if default is not None else {}``
    so that falsy defaults like ``[]`` or ``0`` are preserved.

    Args:
        path: Path to the JSON file.
        default: Value to return when file is missing or invalid. If None, returns ``{}``.

    Returns:
        Parsed JSON (dict/list/etc.) or default.

    Note:
        Used by :func:`load_state_with_fallback`, :func:`load_all_archives`, and
        ``secret_santa_assignments.load_history_from_archives``.
    """
    fallback = default if default is not None else {}
    if path is None or not hasattr(path, "exists"):
        return fallback
    if not path.exists():
        return fallback
    try:
        size = path.stat().st_size
        if size > LOAD_JSON_MAX_BYTES:
            return fallback
        text = path.read_text(encoding='utf-8', errors='replace').strip()
        if not text:
            return fallback
        return json.loads(text)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        pass
    return fallback


def save_json(path: Path, data: Any, logger=None) -> None:
    """
    Save JSON atomically with crash-safe write-temp-replace.

    Writes to ``path.tmp`` first, then atomically replaces the target file.
    If the process crashes mid-write, the original file stays intact.

    Args:
        path: Destination file path.
        data: JSON-serializable data (dict, list, etc.).
        logger: Optional logger for error messages.

    Raises:
        OSError, json.JSONEncodeError: Re-raised after cleanup; caller handles.
    """
    temp = path.with_suffix('.tmp')
    try:
        temp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        # Atomic replace - on Unix/Linux this is guaranteed atomic
        # On Windows, this is the best we can do without fsync
        temp.replace(path)
    except Exception as e:
        # Clean up temp file on error
        if temp.exists():
            try:
                temp.unlink()
            except Exception:
                pass
        if logger:
            logger.error(f"Failed to save JSON to {path}: {e}")
        raise


def get_default_state() -> dict:
    """
    Return the canonical empty/minimal state structure.

    Used when no valid state file exists or validation fails completely.

    Returns:
        Dict with ``current_year`` (today), ``pair_history`` (empty), ``current_event`` (None).
    """
    return {
        "current_year": dt.date.today().year,
        "pair_history": {},
        "current_event": None
    }


def validate_state_structure(state: dict, logger=None) -> dict:
    """
    Ensure state has required keys and valid types; fix in place.

    Validates ``current_year`` (int, 2000–2100), ``pair_history``, ``current_event``,
    and nested event fields. Resets invalid data to defaults.

    Args:
        state: State dict (may be mutated).
        logger: Optional logger for warnings.

    Returns:
        The same dict after validation/fixes.
    """
    if not isinstance(state, dict):
        if logger:
            logger.error("State is not a dict, using defaults")
        return get_default_state()
    
    # Ensure required keys exist and current_year is valid (int, 2000-2100)
    today_year = dt.date.today().year
    raw_year = state.get("current_year")
    if not isinstance(raw_year, int) or raw_year < 2000 or raw_year > 2100:
        if raw_year is not None and logger:
            logger.warning(f"Invalid current_year in state ({raw_year!r}), resetting to {today_year}")
        state["current_year"] = today_year
    if "pair_history" not in state:
        state["pair_history"] = {}
    if "current_event" not in state:
        state["current_event"] = None
    
    # Validate current event if it exists
    current_event = state.get("current_event")
    if current_event:
        if not isinstance(current_event, dict):
            if logger:
                logger.error("Invalid event state - not a dict, resetting")
            state["current_event"] = None
        elif not isinstance(current_event.get("participants"), dict):
            if logger:
                logger.error("Invalid event state - participants not a dict, resetting")
            state["current_event"] = None
        else:
            required_fields = ["active", "participants", "assignments", "guild_id"]
            if not all(field in current_event for field in required_fields):
                if logger:
                    logger.warning("Event missing required fields, may be incomplete")
    
    return state


def load_state_with_fallback(logger=None) -> dict:
    """
    Load Secret Santa state with multi-layer fallback.

    Try: (1) main state file → (2) .backup file → (3) :func:`get_default_state`.
    Ensures the cog always gets valid state, even after corruption or crash.

    Args:
        logger: Optional logger for load status.

    Returns:
        Validated state dict, never raises.
    """
    # Try main state file
    try:
        state = load_json(STATE_FILE, get_default_state())
        state = validate_state_structure(state, logger)
        
        if logger:
            current_event = state.get("current_event")
            active = bool(current_event and current_event.get("active")) if isinstance(current_event, dict) else False
            logger.info(f"State loaded successfully. Active event: {active}")
        
        return state
        
    except Exception as e:
        if logger:
            logger.error(f"Failed to load state: {e}, trying backup", exc_info=True)
    
    # Try backup file
    backup_path = STATE_FILE.with_suffix('.backup')
    if backup_path.exists():
        try:
            if logger:
                logger.info("Attempting to load from backup...")
            state = load_json(backup_path, get_default_state())
            state = validate_state_structure(state, logger)
            if logger:
                logger.info("Backup state loaded successfully")
            return state
        except Exception as backup_error:
            if logger:
                logger.error(f"Backup load also failed: {backup_error}")
    
    # All else failed - use clean defaults
    if logger:
        logger.warning("Using clean default state")
    return get_default_state()


def save_state(state: dict, logger=None) -> bool:
    """
    Persist state to disk; on failure, try saving to .backup.

    Uses :func:`save_json` for atomic writes. If main file fails, attempts
    ``secret_santa_state.backup`` so data is not lost.

    Args:
        state: Full state dict to persist.
        logger: Optional logger for errors.

    Returns:
        True if main file saved; False if both main and backup failed.
    """
    try:
        save_json(STATE_FILE, state, logger)
        return True
    except Exception as e:
        if logger:
            logger.error(f"CRITICAL: Failed to save state: {e}", exc_info=True)
        # Try to save a backup
        try:
            backup_path = STATE_FILE.with_suffix('.backup')
            save_json(backup_path, state, logger)
            if logger:
                logger.warning(f"Saved to backup file: {backup_path}")
        except Exception as backup_error:
            if logger:
                logger.error(f"Backup save also failed: {backup_error}")
        return False


def load_all_archives(logger=None) -> Dict[int, dict]:
    """
    Load all year archives from :data:`ARCHIVE_DIR` into a single dict.

    Scans for ``[0-9]*.json`` (e.g. 2021.json, 2022.json). Skips the backups
    subdirectory. Converts legacy format (assignments list) to unified format
    (event with participants, gift_submissions, assignments).

    Args:
        logger: Optional logger for load errors.

    Returns:
        ``{year: archive_data}`` with unified structure.
    """
    archives = {}
    
    for archive_file in ARCHIVE_DIR.glob("[0-9]*.json"):
        # Skip files in backups subdirectory
        if "backups" in archive_file.parts:
            continue
            
        year_str = archive_file.stem
        
        # Skip non-4-digit year files
        if not year_str.isdigit() or len(year_str) != 4:
            continue
        
        try:
            year_int = int(year_str)
            data = load_json(archive_file)
            if not data:
                continue
            archives[year_int] = normalize_archive(data, year_int, logger=logger)
        
        except Exception as e:
            if logger:
                logger.warning(f"Error loading archive {archive_file}: {e}")
            continue
    
    return archives


def archive_event(event: Dict[str, Any], year: int, logger=None) -> str:
    """
    Archive a completed event to :data:`ARCHIVE_DIR` in unified format.

    Writes to ``{year}.json``. If that file already exists, writes to
    ``{year}_backup_{timestamp}.json`` instead to avoid overwriting.
    Year is clamped to 2000–2100 if invalid.

    Args:
        event: Event dict (participants, assignments, gift_submissions, etc.).
        year: Four-digit year (e.g. 2025).
        logger: Optional logger for status.

    Returns:
        Name of the created file (e.g. ``"2025.json"`` or ``"2025_backup_20250125_123456.json"``).
    """
    if not event or not isinstance(event, dict):
        if logger:
            logger.error("archive_event: event must be a non-empty dict")
        raise ValueError("event must be a non-empty dict")
    # Defensive: ensure year is valid so we never write e.g. 5.json or 99999.json
    today_year = dt.date.today().year
    if not isinstance(year, int) or year < 2000 or year > 2100:
        if logger:
            logger.warning(f"archive_event: invalid year {year!r}, using {today_year}")
        year = today_year
    archive_data = {
        "year": year,
        "event": event.copy(),
        "archived_at": time.time(),
        "timestamp": dt.datetime.now().isoformat()
    }
    
    archive_path = ARCHIVE_DIR / f"{year}.json"
    
    # Prevent data loss from accidental overwrites
    if archive_path.exists():
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = ARCHIVE_DIR / f"{year}_backup_{timestamp}.json"
        save_json(backup_path, archive_data, logger)
        
        if logger:
            logger.warning(f"⚠️ Archive {year}.json already exists! Saved to {backup_path.name} instead")
        
        return backup_path.name
    else:
        save_json(archive_path, archive_data, logger)
        if logger:
            logger.info(f"Archived Secret Santa {year} → {archive_path.name}")
        return archive_path.name


# Export paths for cog usage
__all__ = [
    'ROOT', 'STATE_FILE', 'ARCHIVE_DIR', 'BACKUPS_DIR',
    'TEST_ARCHIVE_YEAR',
    'load_json', 'save_json', 'get_default_state', 'validate_state_structure',
    'load_state_with_fallback', 'save_state', 'load_all_archives', 'archive_event',
    'normalize_archive', 'count_event_participants', 'is_valid_archive_year',
]
