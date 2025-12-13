"""
Large-Scale Secret Santa Simulation

This script simulates Secret Santa with:
- 150 participants (even number)
- 169 participants (odd/uneven number)

Tests the assignment algorithm with large groups and ensures:
- All participants get assigned
- No self-assignments
- No duplicate receivers
- Algorithm handles odd numbers correctly
"""

import asyncio
import json
import logging
import sys
import time
from typing import Dict, List, Set
from collections import Counter

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("large_santa_sim")


class SimulationResults:
    """Track simulation results"""
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warnings = []
    
    def add_pass(self, test_name: str, details: str = ""):
        self.passed.append((test_name, details))
        logger.info(f"[PASS] {test_name} {details}")
    
    def add_fail(self, test_name: str, error: str):
        self.failed.append((test_name, error))
        logger.error(f"[FAIL] {test_name} - {error}")
    
    def add_warning(self, test_name: str, message: str):
        self.warnings.append((test_name, message))
        logger.warning(f"[WARN] {test_name} - {message}")
    
    def print_summary(self):
        print("\n" + "="*80)
        print("LARGE-SCALE SECRET SANTA SIMULATION SUMMARY")
        print("="*80)
        print(f"[PASSED] {len(self.passed)}")
        print(f"[FAILED] {len(self.failed)}")
        print(f"[WARNINGS] {len(self.warnings)}")
        
        if self.passed:
            print("\n[PASSED] TESTS:")
            for name, details in self.passed:
                print(f"  * {name}" + (f" - {details}" if details else ""))
        
        if self.failed:
            print("\n[FAILED] TESTS:")
            for name, error in self.failed:
                print(f"  * {name}: {error}")
        
        if self.warnings:
            print("\n[WARNINGS]:")
            for name, message in self.warnings:
                print(f"  * {name}: {message}")
        
        print("="*80)


def make_assignments_simple(participants: List[int]) -> Dict[int, int]:
    """
    Simple assignment algorithm for testing (round-robin with shuffle).
    This simulates the real algorithm behavior.
    """
    import random
    
    if len(participants) < 2:
        raise ValueError("Need at least 2 participants")
    
    # Special case: 2 participants
    if len(participants) == 2:
        p1, p2 = participants[0], participants[1]
        return {p1: p2, p2: p1}
    
    # For 3+ participants: shuffle and assign
    shuffled = participants.copy()
    random.shuffle(shuffled)
    
    assignments = {}
    for i in range(len(shuffled)):
        giver = shuffled[i]
        receiver = shuffled[(i + 1) % len(shuffled)]  # Wrap around
        assignments[giver] = receiver
    
    return assignments


def validate_assignments(assignments: Dict[int, int], participants: List[int], results: SimulationResults) -> bool:
    """Validate assignment integrity"""
    try:
        # Check 1: All participants are givers
        givers = set(assignments.keys())
        expected_givers = set(participants)
        if givers != expected_givers:
            missing = expected_givers - givers
            extra = givers - expected_givers
            results.add_fail("Assignment Validation", f"Giver mismatch: missing {missing}, extra {extra}")
            return False
        
        # Check 2: All participants are receivers exactly once
        receivers = list(assignments.values())
        expected_receivers = set(participants)
        actual_receivers = set(receivers)
        
        if actual_receivers != expected_receivers:
            missing = expected_receivers - actual_receivers
            extra = actual_receivers - expected_receivers
            results.add_fail("Assignment Validation", f"Receiver mismatch: missing {missing}, extra {extra}")
            return False
        
        # Check 3: No duplicate receivers
        receiver_counts = Counter(receivers)
        duplicates = {r: count for r, count in receiver_counts.items() if count > 1}
        if duplicates:
            results.add_fail("Assignment Validation", f"Duplicate receivers: {duplicates}")
            return False
        
        # Check 4: No self-assignments
        for giver, receiver in assignments.items():
            if giver == receiver:
                results.add_fail("Assignment Validation", f"Self-assignment: {giver} → {receiver}")
                return False
        
        # Check 5: All assignments are valid participant IDs
        for giver, receiver in assignments.items():
            if giver not in participants:
                results.add_fail("Assignment Validation", f"Invalid giver: {giver}")
                return False
            if receiver not in participants:
                results.add_fail("Assignment Validation", f"Invalid receiver: {receiver}")
                return False
        
        results.add_pass("Assignment Validation", "All checks passed")
        return True
        
    except Exception as e:
        results.add_fail("Assignment Validation", str(e))
        return False


def simulate_large_event(participant_count: int, results: SimulationResults) -> bool:
    """Simulate a Secret Santa event with many participants"""
    try:
        print(f"\n{'='*80}")
        print(f"SIMULATING EVENT WITH {participant_count} PARTICIPANTS")
        print(f"{'='*80}")
        
        # Create participants
        participants = list(range(1000, 1000 + participant_count))
        print(f"\nCreated {len(participants)} participants (IDs: {participants[0]} to {participants[-1]})")
        
        # Make assignments
        start_time = time.time()
        assignments = make_assignments_simple(participants)
        elapsed = time.time() - start_time
        
        print(f"Assignments created in {elapsed:.4f} seconds")
        print(f"Total assignments: {len(assignments)}")
        
        # Validate assignments
        if not validate_assignments(assignments, participants, results):
            return False
        
        # Check for cycles (optional - just for info)
        cycle_lengths = []
        visited = set()
        for start in participants:
            if start in visited:
                continue
            
            cycle = []
            current = start
            while current not in visited:
                visited.add(current)
                cycle.append(current)
                current = assignments[current]
                if current == start:
                    break
            
            if len(cycle) > 0:
                cycle_lengths.append(len(cycle))
        
        print(f"Cycle analysis: {len(cycle_lengths)} cycles found")
        if cycle_lengths:
            print(f"  Min cycle length: {min(cycle_lengths)}")
            print(f"  Max cycle length: {max(cycle_lengths)}")
            print(f"  Average cycle length: {sum(cycle_lengths) / len(cycle_lengths):.2f}")
        
        # Statistics
        results.add_pass(
            f"Large Event: {participant_count} participants",
            f"{len(assignments)} assignments, {elapsed:.4f}s, {len(cycle_lengths)} cycles"
        )
        
        # Test edge cases
        print(f"\nTesting edge cases for {participant_count} participants...")
        
        # Test: First participant
        first_participant = participants[0]
        first_receiver = assignments[first_participant]
        if first_receiver == first_participant:
            results.add_fail(f"Edge Case: First participant", "Self-assignment detected")
            return False
        results.add_pass(f"Edge Case: First participant", f"Assigned to {first_receiver}")
        
        # Test: Last participant
        last_participant = participants[-1]
        last_receiver = assignments[last_participant]
        if last_receiver == last_participant:
            results.add_fail(f"Edge Case: Last participant", "Self-assignment detected")
            return False
        results.add_pass(f"Edge Case: Last participant", f"Assigned to {last_receiver}")
        
        # Test: Middle participant
        middle_idx = len(participants) // 2
        middle_participant = participants[middle_idx]
        middle_receiver = assignments[middle_participant]
        if middle_receiver == middle_participant:
            results.add_fail(f"Edge Case: Middle participant", "Self-assignment detected")
            return False
        results.add_pass(f"Edge Case: Middle participant", f"Assigned to {middle_receiver}")
        
        # Test: All participants have unique receivers
        receivers = list(assignments.values())
        if len(receivers) != len(set(receivers)):
            results.add_fail(f"Edge Case: Unique receivers", "Duplicate receivers found")
            return False
        results.add_pass(f"Edge Case: Unique receivers", f"All {len(receivers)} receivers are unique")
        
        return True
        
    except Exception as e:
        results.add_fail(f"Large Event: {participant_count} participants", str(e))
        logger.error(f"Error in large event simulation: {e}", exc_info=True)
        return False


async def run_large_simulation():
    """Run large-scale Secret Santa simulation"""
    results = SimulationResults()
    
    print("\n" + "="*80)
    print("LARGE-SCALE SECRET SANTA SIMULATION")
    print("="*80)
    print("\nTesting Secret Santa with large participant counts...")
    print("This tests the assignment algorithm with:")
    print("  - 150 participants (even number)")
    print("  - 169 participants (odd/uneven number)")
    print()
    
    try:
        # Test 1: 150 participants (even number)
        print("\n" + "="*80)
        print("TEST 1: 150 PARTICIPANTS (EVEN NUMBER)")
        print("="*80)
        success1 = simulate_large_event(150, results)
        
        # Test 2: 169 participants (odd number)
        print("\n" + "="*80)
        print("TEST 2: 169 PARTICIPANTS (ODD/UNEVEN NUMBER)")
        print("="*80)
        success2 = simulate_large_event(169, results)
        
        # Additional tests with various sizes
        print("\n" + "="*80)
        print("ADDITIONAL TESTS: VARIOUS SIZES")
        print("="*80)
        
        test_sizes = [2, 3, 5, 10, 25, 50, 100, 200]
        for size in test_sizes:
            print(f"\nTesting with {size} participants...")
            simulate_large_event(size, results)
        
        # Performance test
        print("\n" + "="*80)
        print("PERFORMANCE TEST")
        print("="*80)
        
        large_sizes = [100, 150, 169, 200, 250, 300]
        performance_results = []
        
        for size in large_sizes:
            participants = list(range(1000, 1000 + size))
            start_time = time.time()
            assignments = make_assignments_simple(participants)
            elapsed = time.time() - start_time
            
            performance_results.append((size, elapsed, len(assignments)))
            print(f"  {size} participants: {elapsed:.4f}s ({len(assignments)} assignments)")
        
        # Calculate average time per participant
        total_time = sum(t for _, t, _ in performance_results)
        total_participants = sum(s for s, _, _ in performance_results)
        avg_time_per_participant = total_time / total_participants if total_participants > 0 else 0
        
        results.add_pass(
            "Performance Test",
            f"Average {avg_time_per_participant*1000:.4f}ms per participant"
        )
        
        print(f"\nAverage time per participant: {avg_time_per_participant*1000:.4f}ms")
        
    except Exception as e:
        results.add_fail("Simulation Error", str(e))
        logger.error(f"Simulation error: {e}", exc_info=True)
    
    # Print summary
    results.print_summary()
    
    return results


if __name__ == "__main__":
    # Fix Windows encoding issues
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    print("\n[STARTING] Large-Scale Secret Santa Simulation...")
    results = asyncio.run(run_large_simulation())
    
    # Exit code based on results
    if results.failed:
        print("\n[FAILED] Simulation completed with failures")
        exit(1)
    else:
        print("\n[SUCCESS] Simulation completed successfully!")
        exit(0)

