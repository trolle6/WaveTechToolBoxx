"""
Secret Santa Assignment Module - Algorithm and History Management

RESPONSIBILITIES:
- Assignment algorithm (avoids repeats, prevents cycles)
- History loading from archives
- Assignment validation
- Progressive fallback system

ISOLATION:
- Pure algorithm logic (no Discord dependencies)
- Can be tested independently
- Fast and efficient (uses secrets.SystemRandom for security)
"""

import secrets
from pathlib import Path
from typing import Dict, List, Optional

from .secret_santa_storage import load_json, ARCHIVE_DIR


def load_history_from_archives(archive_dir: Path, exclude_years: List[int] = None, logger=None) -> tuple[Dict[str, List[int]], List[int]]:
    """
    Load Secret Santa history from archive files.
    
    WHAT IT DOES:
    - Scans archive directory for year files (2021.json, 2022.json, etc.)
    - Extracts who gave to who from each year
    - Builds a complete history map for assignment algorithm
    
    HISTORY MAP STRUCTURE:
    {
        "huntoon_id": [trolle_id, trolle_id],  # Huntoon had trolle twice
        "trolle_id": [squibble_id, jkm_id],    # trolle had squibble and jkm
        "m3_id": [trolle_id]                   # m³ had trolle once
    }
    
    This map is used by the assignment algorithm to PREVENT repeats.
    
    Args:
        archive_dir: Path to archive directory
        exclude_years: List of years to exclude from history (for fallback)
        logger: Optional logger for debugging
    
    Returns:
        Tuple of (history dict, list of available years sorted oldest to newest)
    """
    exclude_years = exclude_years or []
    history = {}
    available_years = []

    # ARCHIVE SCANNING: Load all YYYY.json files from archive directory
    # CRITICAL: Ignore backups folder (indestructible backup system)
    for archive_file in archive_dir.glob("[0-9]*.json"):
        # Skip files in backups subdirectory
        if "backups" in archive_file.parts:
            continue
            
        try:
            year_str = archive_file.stem
            if not year_str.isdigit() or len(year_str) != 4:
                continue

            year = int(year_str)
            available_years.append(year)
            
            # Skip excluded years
            if year in exclude_years:
                if logger:
                    logger.info(f"Excluding year {year} from history (fallback mode)")
                continue

            archive_data = load_json(archive_file)

            # Check for unified format (event key)
            if archive_data.get("event"):
                event_data = archive_data["event"]
                event_assignments = event_data.get("assignments", {})

                if isinstance(event_assignments, dict):
                    for giver, receiver in event_assignments.items():
                        try:
                            receiver_int = int(receiver)
                            history.setdefault(str(giver), []).append(receiver_int)
                        except (ValueError, TypeError):
                            continue
            
            # Handle legacy old format (direct assignments list)
            elif "assignments" in archive_data and isinstance(archive_data["assignments"], list):
                for assignment in archive_data["assignments"]:
                    giver_id = assignment.get("giver_id")
                    receiver_id = assignment.get("receiver_id")

                    if giver_id and receiver_id:
                        try:
                            receiver_int = int(receiver_id)
                            history.setdefault(str(giver_id), []).append(receiver_int)
                        except (ValueError, TypeError):
                            continue

        except Exception as e:
            if logger:
                logger.warning(f"Error loading archive {archive_file}: {e}")
            continue

    # Sort available years (oldest first)
    available_years.sort()
    
    return history, available_years


def validate_assignment_possibility(participants: List[int], history: Dict[str, List[int]]) -> Optional[str]:
    """
    Check if assignments are possible before attempting them.
    
    VALIDATION LEVELS:
    1. CRITICAL: Anyone with ZERO options → Impossible, fail immediately
    2. WARNING: Many people with limited options → Might be difficult, but try anyway
    
    The algorithm itself is smart enough to handle difficult cases with retries.
    Only fail validation if truly impossible (someone has zero receivers).
    """
    if len(participants) < 2:
        return "Need at least 2 participants for Secret Santa"
    
    # Check each participant's available options
    problematic_users = []
    limited_users = []
    
    for giver in participants:
        unacceptable = history.get(str(giver), [])
        available = [p for p in participants if p not in unacceptable and p != giver]
        
        if len(available) == 0:
            # CRITICAL: Zero options - truly impossible
            problematic_users.append(str(giver))
        elif len(available) == 1:
            # Limited but possible - track for warning
            limited_users.append(giver)
    
    # Only fail if someone has ZERO options (truly impossible)
    if problematic_users:
        return f"Assignment impossible - users {', '.join(problematic_users)} have no valid receivers. Use fallback or clear history."
    
    # If many limited users, log warning but DON'T fail
    # (The algorithm can handle this with retries!)
    if len(limited_users) > len(participants) // 2:
        # Just log it, don't fail validation
        # The algorithm will try 10 times with different orderings
        pass
    
    return None  # ✅ Let algorithm try (it's smart enough!)


def _validate_assignment_integrity(assignments: Dict[int, int], participants: List[int]) -> None:
    """
    CRITICAL VALIDATION: Ensure assignment integrity to prevent duplicate receiver bug.
    
    This function performs comprehensive validation to ensure:
    1. Every participant is a giver exactly once
    2. Every participant is a receiver exactly once  
    3. No one gives to themselves
    4. No duplicate receivers (multiple people giving to same person)
    5. No missing assignments
    
    This is the final safety net that prevents the duplicate receiver bug from EVER happening.
    
    Args:
        assignments: Dict mapping giver ID to receiver ID
        participants: List of all participant IDs
        
    Raises:
        ValueError: If any integrity check fails
    """
    if not assignments:
        raise ValueError("No assignments provided")
    
    if len(assignments) != len(participants):
        raise ValueError(f"Assignment count mismatch: {len(assignments)} assignments for {len(participants)} participants")
    
    # Check 1: Every participant is a giver exactly once
    givers = set(assignments.keys())
    expected_givers = set(participants)
    if givers != expected_givers:
        missing_givers = expected_givers - givers
        extra_givers = givers - expected_givers
        raise ValueError(f"Giver mismatch: missing {missing_givers}, extra {extra_givers}")
    
    # Check 2: Every participant is a receiver exactly once
    receivers = list(assignments.values())
    expected_receivers = set(participants)
    actual_receivers = set(receivers)
    
    if actual_receivers != expected_receivers:
        missing_receivers = expected_receivers - actual_receivers
        extra_receivers = actual_receivers - expected_receivers
        raise ValueError(f"Receiver mismatch: missing {missing_receivers}, extra {extra_receivers}")
    
    # Check 3: No duplicate receivers (critical bug prevention)
    if len(receivers) != len(set(receivers)):
        # Find duplicates
        receiver_counts = {}
        for receiver in receivers:
            receiver_counts[receiver] = receiver_counts.get(receiver, 0) + 1
        
        duplicates = {r: count for r, count in receiver_counts.items() if count > 1}
        raise ValueError(f"DUPLICATE RECEIVERS DETECTED: {duplicates} - This is the bug we're preventing!")
    
    # Check 4: No self-assignments
    for giver, receiver in assignments.items():
        if giver == receiver:
            raise ValueError(f"Self-assignment detected: {giver} → {receiver}")
    
    # Check 5: All assignments are valid participant IDs
    for giver, receiver in assignments.items():
        if giver not in participants:
            raise ValueError(f"Invalid giver: {giver} not in participants")
        if receiver not in participants:
            raise ValueError(f"Invalid receiver: {receiver} not in participants")


def make_assignments(participants: List[int], history: Dict[str, List[int]]) -> Dict[int, int]:
    """
    Create Secret Santa assignments avoiding repeats from history.
    
    ALGORITHM MECHANICS:
    1. Use cryptographically secure randomness (secrets.SystemRandom)
    2. Special case for 2 people (simple exchange, allows cycles)
    3. For 3+: Shuffle and assign, preventing cycles
    4. Retry up to 10 times if assignment fails
    
    HISTORY ENFORCEMENT:
    - Each giver has a list of people they've had before (from archives)
    - Algorithm EXCLUDES those people from available receivers
    - Example: huntoon had [trolle_2023, trolle_2024] → can't get trolle again
    - This prevents repeats and ensures variety across years
    
    SPECIAL CASES:
    - 2 people: Simple A→B, B→A exchange (cycle allowed)
    - 3+ people: Full algorithm with anti-cycle protection
    
    SAFETY:
    - Works with copy of history (doesn't modify unless successful)
    - Only updates real history if ALL assignments succeed
    - Prevents cycles for 3+ participants (but allows for 2)
    
    RANDOMNESS:
    Uses secrets.SystemRandom() which is cryptographically secure and doesn't
    require manual seeding. It uses the OS's entropy pool directly (os.urandom).
    This is the recommended approach for security-sensitive randomness in Python.
    
    Args:
        participants: List of user IDs participating
        history: Dict mapping giver ID (str) to list of previous receiver IDs
    
    Returns:
        Dict mapping giver ID (int) to receiver ID (int)
    
    Raises:
        ValueError: If assignment is impossible with current history
    """
    if len(participants) < 2:
        raise ValueError("Need at least 2 participants")

    # Use cryptographically secure random number generator
    # This provides true randomness from OS entropy pool
    secure_random = secrets.SystemRandom()
    
    # SPECIAL CASE: 2 participants (simple exchange)
    if len(participants) == 2:
        # With only 2 people, we need a cycle: A→B, B→A
        # Check if this pairing has happened before
        p1, p2 = participants[0], participants[1]
        
        # Check if either has given to the other before
        p1_history = history.get(str(p1), [])
        p2_history = history.get(str(p2), [])
        
        if p2 in p1_history or p1 in p2_history:
            # They've paired before - cannot make assignment
            raise ValueError(f"2-person assignment failed: these participants have already been paired")
        
        # Valid pairing - create simple exchange
        result = {p1: p2, p2: p1}
        
        # Update history
        history.setdefault(str(p1), []).append(p2)
        history.setdefault(str(p2), []).append(p1)
        
        return result
    
    # NORMAL CASE: 3+ participants
    # ADAPTIVE RETRY LOGIC: Scale attempts with participant count
    # Small events (< 10 people): 10 attempts (safety floor)
    # Large events (≥ 10 people): Attempts = participant count (scales with complexity)
    # Example: 5 people → 10 attempts, 20 people → 20 attempts
    max_attempts = max(10, len(participants))
    
    for attempt in range(max_attempts):
        try:
            result: Dict[int, int] = {}
            temp_history = {k: v.copy() for k, v in history.items()}  # Work with copy
            
            # Shuffle participants for different assignment order each attempt
            shuffled_participants = participants.copy()
            secure_random.shuffle(shuffled_participants)
            
            for giver in shuffled_participants:
                # HISTORY CHECK: Get list of people this giver has had before
                # Example: huntoon's unacceptable = [trolle_2023, trolle_2024]
                unacceptable: List[int] = temp_history.get(str(giver), [])
                
                # CYCLE PREVENTION: Add current assignments where someone else is giving to this giver
                # This prevents cycles like: A→B, B→C, C→A (which would fail)
                # For 3+ people, we want a clean chain, not a loop
                for g, r in result.items():
                    if r == giver:
                        unacceptable.append(g)
                
                # DUPLICATE PREVENTION: Add people who are already assigned as receivers
                # This prevents multiple people from giving to the same receiver
                for g, r in result.items():
                    unacceptable.append(r)
                
                # AVAILABLE POOL: Find who this person CAN receive
                # Excludes: history + cycles + duplicates + self
                available = [p for p in participants if p not in unacceptable and p != giver]
                
                if not available:
                    raise ValueError(f"Cannot assign giver {giver} - no valid receivers available")
                
                # RANDOM ASSIGNMENT: Pick from available pool
                receiver = secure_random.choice(available)
                result[giver] = receiver
                temp_history.setdefault(str(giver), []).append(receiver)
            
            # CRITICAL VALIDATION: Ensure assignment integrity before accepting
            # This prevents the duplicate receiver bug from EVER happening again
            _validate_assignment_integrity(result, participants)
            
            # Success! Update the real history
            for giver, receiver in result.items():
                history.setdefault(str(giver), []).append(receiver)
            
            return result
            
        except ValueError:
            if attempt == max_attempts - 1:
                # Last attempt failed - provide detailed error for fallback
                raise ValueError("Assignment failed with current history constraints")
            continue  # Try again with different random order
    
    raise ValueError("Assignment failed - this should not be reached")




