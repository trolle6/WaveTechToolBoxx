"""
Secret Santa Storage Module - File I/O and State Management

RESPONSIBILITIES:
- JSON file operations (load/save with atomic writes)
- State file management with fallback
- Archive operations and loading
- Cross-platform compatibility (Windows/Linux)
- Health monitoring (disk space, permissions, early warnings)

ISOLATION:
- No Discord dependencies
- Pure file/state operations
- Can be tested independently
"""

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional



# Paths - relative to cogs directory
ROOT = Path(__file__).parent
STATE_FILE = ROOT / "secret_santa_state.json"
ARCHIVE_DIR = ROOT / "archive"
BACKUPS_DIR = ARCHIVE_DIR / "backups"

# Ensure directories exist
ARCHIVE_DIR.mkdir(exist_ok=True)
BACKUPS_DIR.mkdir(exist_ok=True)


def load_json(path: Path, default: Any = None) -> Any:
    """Load JSON with error handling (cross-platform compatible)"""
    if path.exists():
        try:
            # Explicit UTF-8 encoding for cross-platform compatibility
            text = path.read_text(encoding='utf-8').strip()
            return json.loads(text) if text else (default or {})
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    return default or {}


def save_json(path: Path, data: Any, logger=None):
    """
    Save JSON atomically with error handling and health checks (cross-platform compatible).
    
    FEATURES:
    - Proactive disk space and permission checks before writing
    - Early warnings if operation may fail
    - Atomic writes (temp file + rename for safety)
    """
    # Health check before operation (proactive failure detection)
    monitor = _get_health_monitor(logger)
    if monitor:
        is_safe, warning = monitor.validate_path_safety(path, operation="write")
        if not is_safe:
            # Critical failure - cannot proceed
            if logger:
                logger.error(f"Cannot save {path}: {warning}")
            raise OSError(f"Health check failed: {warning}")
        elif warning and logger:
            # Warning but safe to proceed
            logger.warning(f"Health check warning for {path}: {warning}")
    
    temp = path.with_suffix('.tmp')
    try:
        # Explicit UTF-8 encoding for cross-platform compatibility
        temp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        temp.replace(path)
    except Exception as e:
        # Clean up temp file if save failed
        if temp.exists():
            try:
                temp.unlink()
            except Exception:
                pass
        raise  # Re-raise so caller knows save failed


def get_default_state() -> dict:
    """
    Get default state structure (DRY - used in multiple places).
    Extracted to avoid repetition in fallback logic.
    """
    return {
        "current_year": dt.date.today().year,
        "pair_history": {},
        "current_event": None
    }


def validate_state_structure(state: dict, logger=None) -> dict:
    """
    Validate and fix state structure.
    
    Returns: Validated state (repaired if needed)
    """
    # Ensure it's actually a dict
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
            # Check for required fields
            required_fields = ["active", "participants", "assignments", "guild_id"]
            if not all(field in current_event for field in required_fields):
                if logger:
                    logger.warning("Event missing required fields, may be incomplete")
    
    return state


def load_state_with_fallback(logger=None) -> dict:
    """
    Load state with multi-layer fallback system.
    
    Fallback chain:
    1. Load main state file
    2. Validate structure
    3. If corrupted → Try backup file
    4. If backup fails → Use clean defaults
    
    Returns: Valid state dict (guaranteed)
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
    Save state to disk with error handling and backup.
    
    Returns: True if successful, False otherwise
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
    Load all archive files from archive directory.
    
    Handles both:
    - Current unified format (event key with full data)
    - Legacy format (assignments list)
    
    Returns: Dict mapping year → archive data
    """
    archives = {}
    
    for archive_file in ARCHIVE_DIR.glob("[0-9]*.json"):
        # Skip files in backups subdirectory (indestructible backup system)
        if "backups" in archive_file.parts:
            continue
            
        year_str = archive_file.stem
        
        # Skip non-4-digit year files (e.g., backup files)
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
                    gift = assignment.get("gift", "No description")
                    
                    participants[giver_id] = giver_name
                    if receiver_id:
                        participants[receiver_id] = receiver_name
                    
                    # Only add gifts if there's actual data
                    if gift and gift != "No description":
                        gifts[giver_id] = {
                            "gift": gift,
                            "receiver_name": receiver_name,
                            "receiver_id": receiver_id
                        }
                    
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


# Export paths for cog usage
__all__ = [
    'ROOT', 'STATE_FILE', 'ARCHIVE_DIR', 'BACKUPS_DIR',
    'load_json', 'save_json', 'get_default_state', 'validate_state_structure',
    'load_state_with_fallback', 'save_state', 'load_all_archives', 'archive_event'
]


def archive_event(event: Dict[str, Any], year: int, logger=None) -> str:
    """
    Archive event data in unified format with CRITICAL overwrite protection.
    
    ARCHIVE PROTECTION MECHANICS:
    - Checks if YYYY.json already exists (e.g., 2025.json)
    - If exists: Saves to timestamped backup instead (2025_backup_20251216_153045.json)
    - If new: Saves normally to YYYY.json
    - NEVER overwrites existing archives (prevents data loss!)
    
    Returns:
        Filename of the saved archive (e.g., "2025.json" or "2025_backup_20251216_153045.json")
    """
    archive_data = {
        "year": year,
        "event": event.copy(),
        "archived_at": time.time(),
        "timestamp": dt.datetime.now().isoformat()
    }
    
    archive_path = ARCHIVE_DIR / f"{year}.json"
    
    # CRITICAL SAFETY CHECK: Prevent data loss from accidental overwrites
    if archive_path.exists():
        # Archive already exists! Save to backup file instead (NEVER overwrite!)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = ARCHIVE_DIR / f"{year}_backup_{timestamp}.json"
        save_json(backup_path, archive_data, logger)
        
        if logger:
            logger.warning(f"⚠️ Archive {year}.json already exists! Saved to {backup_path.name} instead")
            logger.warning(f"This suggests you ran multiple events in {year}. Please review archives manually!")
        
        return backup_path.name
    else:
        # Safe to save normally
        save_json(archive_path, archive_data, logger)
        if logger:
            logger.info(f"Archived Secret Santa {year} → {archive_path.name}")
        return archive_path.name

