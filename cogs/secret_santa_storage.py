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


# Max file size to prevent DoS from huge/corrupt files (10MB is plenty for state/archives)
LOAD_JSON_MAX_BYTES = 10 * 1024 * 1024


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
            
            # Check for unified format (event key)
            if data and "event" in data:
                archives[year_int] = data
            
            # Handle legacy format (assignments list)
            elif data and "assignments" in data and isinstance(data["assignments"], list):
                # Convert to unified format
                participants = {}
                gifts = {}
                assignments_map = {}
                
                for assignment in data["assignments"]:
                    if not isinstance(assignment, dict):
                        continue
                    giver_id = assignment.get("giver_id", "")
                    giver_name = assignment.get("giver_name", "Unknown")
                    receiver_id = assignment.get("receiver_id", "")
                    receiver_name = assignment.get("receiver_name", "Unknown")
                    gift = assignment.get("gift")
                    # Only include in gift_submissions when gift is a non-empty string (handles null/empty/legacy)
                    if isinstance(gift, str) and gift.strip():
                        gifts[giver_id] = {
                            "gift": gift,
                            "receiver_name": receiver_name,
                            "receiver_id": receiver_id
                        }
                    
                    participants[giver_id] = giver_name
                    if receiver_id:
                        participants[receiver_id] = receiver_name
                    if giver_id and receiver_id:
                        assignments_map[giver_id] = receiver_id
                
                # Convert to unified structure
                archives[year_int] = {
                    "year": year_int,
                    "event": {
                        "participants": participants,
                        "gift_submissions": gifts,
                        "assignments": assignments_map
                    }
                }
        
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
    'load_json', 'save_json', 'get_default_state', 'validate_state_structure',
    'load_state_with_fallback', 'save_state', 'load_all_archives', 'archive_event'
]
