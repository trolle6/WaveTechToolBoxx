"""
Secret Santa Storage Module - File I/O and State Management

RESPONSIBILITIES:
- JSON file operations (load/save with atomic writes)
- State file management with fallback
- Archive operations and loading
- Cross-platform compatibility (Windows/Linux)
"""

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Paths - relative to cogs directory
ROOT = Path(__file__).parent
STATE_FILE = ROOT / "secret_santa_state.json"
ARCHIVE_DIR = ROOT / "archive"
BACKUPS_DIR = ARCHIVE_DIR / "backups"

# Ensure directories exist
ARCHIVE_DIR.mkdir(exist_ok=True)
BACKUPS_DIR.mkdir(exist_ok=True)


def load_json(path: Path, default: Any = None) -> Any:
    """Load JSON with error handling"""
    if path.exists():
        try:
            text = path.read_text(encoding='utf-8').strip()
            return json.loads(text) if text else (default or {})
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return default or {}


def save_json(path: Path, data: Any, logger=None):
    """
    Save JSON atomically with error handling.
    
    Uses write-temp-replace pattern to ensure atomic writes:
    writes to temporary file first, then replaces original.
    This prevents corruption if process crashes during write.
    
    Args:
        path: File path to save to
        data: Data to serialize (must be JSON-serializable)
        logger: Optional logger for error reporting
    
    Raises:
        Exception: If write fails (caller should handle)
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
    """Get default state structure"""
    return {
        "current_year": dt.date.today().year,
        "pair_history": {},
        "current_event": None
    }


def validate_state_structure(state: dict, logger=None) -> dict:
    """Validate and fix state structure"""
    if not isinstance(state, dict):
        if logger:
            logger.error("State is not a dict, using defaults")
        return get_default_state()
    
    # Ensure required keys exist
    if "current_year" not in state:
        state["current_year"] = dt.date.today().year
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
    """Load state with multi-layer fallback system"""
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
    """Save state to disk with error handling and backup"""
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
    """Load all archive files from archive directory"""
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
    Archive event data in unified format with overwrite protection.
    
    If archive already exists for this year, saves to timestamped backup
    instead to prevent accidental data loss. This allows archiving multiple
    times safely (e.g., if event is restarted or needs correction).
    
    Args:
        event: Event data dictionary (participants, assignments, gifts, etc.)
        year: Year of the event (4-digit integer)
        logger: Optional logger for status messages
    
    Returns:
        Filename of the created archive (either {year}.json or {year}_backup_TIMESTAMP.json)
    """
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
