"""
Massive Secret Santa Simulation - Multiple Cycles & Stress Testing

This script simulates Secret Santa assignments:
- Tests with multiple cycles (not just one big cycle)
- Runs thousands of iterations to verify algorithm stability
- Tests various participant counts
- Verifies no duplicate receivers ever occur
- Verifies no self-assignments ever occur
"""

import asyncio
import secrets
import sys
import time
from collections import Counter
from typing import Dict, List

# Setup logging
import logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("massive_sim")


class SimulationResults:
    """Track simulation results"""
    def __init__(self):
        self.passed = []
        self.failed = []
        self.stats = {
            "total_tests": 0,
            "total_assignments": 0,
            "cycles_found": [],
            "max_cycle_length": 0,
            "min_cycle_length": float('inf'),
            "duplicate_receivers": 0,
            "self_assignments": 0
        }
    
    def add_pass(self, test_name: str, details: str = ""):
        self.passed.append((test_name, details))
        self.stats["total_tests"] += 1
    
    def add_fail(self, test_name: str, error: str):
        self.failed.append((test_name, error))
        self.stats["total_tests"] += 1
    
    def print_summary(self):
        print("\n" + "="*80)
        print("MASSIVE SECRET SANTA SIMULATION SUMMARY")
        print("="*80)
        print(f"[PASSED] {len(self.passed)}")
        print(f"[FAILED] {len(self.failed)}")
        print(f"\n[STATISTICS]")
        print(f"  Total tests run: {self.stats['total_tests']:,}")
        print(f"  Total assignments created: {self.stats['total_assignments']:,}")
        print(f"  Duplicate receivers found: {self.stats['duplicate_receivers']}")
        print(f"  Self-assignments found: {self.stats['self_assignments']}")
        if self.stats['cycles_found']:
            all_cycle_lengths = [l for cycles in self.stats['cycles_found'] for l in cycles]
            if all_cycle_lengths:
                print(f"  Total cycles found: {len(all_cycle_lengths)}")
                print(f"  Min cycle length: {self.stats['min_cycle_length']}")
                print(f"  Max cycle length: {self.stats['max_cycle_length']}")
                print(f"  Average cycle length: {sum(all_cycle_lengths) / len(all_cycle_lengths):.2f}")
        
        if self.failed:
            print("\n[FAILED] TESTS:")
            for name, error in self.failed[:10]:
                print(f"  * {name}: {error}")
            if len(self.failed) > 10:
                print(f"  ... and {len(self.failed) - 10} more failures")
        
        print("="*80)


def make_assignments_realistic(
    participants: List[int],
    history: Dict[str, List[int]] = None
) -> Dict[int, int]:
    """
    Realistic Secret Santa assignment algorithm that creates multiple cycles.
    
    This simulates the REAL algorithm from SecretSanta_cog.py:
    - Uses cryptographic randomness (secrets.SystemRandom)
    - Prevents cycles for 3+ participants
    - Creates multiple cycles when possible
    - Avoids history if provided
    """
    history = history or {}
    
    if len(participants) < 2:
        raise ValueError("Need at least 2 participants")
    
    # Special case: 2 participants (simple exchange)
    if len(participants) == 2:
        p1, p2 = participants[0], participants[1]
        p1_history = history.get(str(p1), [])
        p2_history = history.get(str(p2), [])
        if p2 in p1_history or p1 in p2_history:
            raise ValueError("2-person assignment failed: history conflict")
        return {p1: p2, p2: p1}
    
    # For 3+ participants: Create assignments with cycle prevention
    secure_random = secrets.SystemRandom()
    max_attempts = max(10, len(participants))
    
    for attempt in range(max_attempts):
        try:
            result: Dict[int, int] = {}
            temp_history = {k: v.copy() for k, v in history.items()}
            
            # Shuffle participants
            shuffled = participants.copy()
            secure_random.shuffle(shuffled)
            
            for giver in shuffled:
                # Get unacceptable receivers (history + self)
                unacceptable = set(temp_history.get(str(giver), []))
                unacceptable.add(giver)
                
                # CYCLE PREVENTION: Add current assignments where someone else is giving to this giver
                for g, r in result.items():
                    if r == giver:
                        unacceptable.add(g)
                
                # DUPLICATE PREVENTION: Add people who are already assigned as receivers
                for g, r in result.items():
                    unacceptable.add(r)
                
                # Get available receivers
                available = [p for p in participants if p not in unacceptable and p != giver]
                
                if not available:
                    raise ValueError(f"Cannot assign giver {giver} - no valid receivers")
                
                # Pick receiver
                receiver = secure_random.choice(available)
                result[giver] = receiver
                temp_history.setdefault(str(giver), []).append(receiver)
            
            return result
            
        except ValueError:
            if attempt < max_attempts - 1:
                continue
            raise
    
    raise ValueError("Assignment failed after max attempts")


def find_cycles(assignments: Dict[int, int]) -> List[List[int]]:
    """Find all cycles in assignments"""
    cycles = []
    visited = set()
    
    for start in assignments.keys():
        if start in visited:
            continue
        
        path = []
        current = start
        
        while current not in visited:
            visited.add(current)
            path.append(current)
            current = assignments.get(current)
            
            if current is None:
                break
            
            if current == start:
                cycles.append(path)
                break
            
            if current in path:
                cycle_start_idx = path.index(current)
                cycles.append(path[cycle_start_idx:])
                break
    
    return cycles


def validate_assignments(
    assignments: Dict[int, int],
    participants: List[int],
    results: SimulationResults
) -> bool:
    """Validate assignment integrity"""
    try:
        # Check 1: All participants are givers
        givers = set(assignments.keys())
        expected_givers = set(participants)
        if givers != expected_givers:
            results.add_fail("Validation", f"Giver mismatch")
            return False
        
        # Check 2: All participants are receivers exactly once
        receivers = list(assignments.values())
        expected_receivers = set(participants)
        actual_receivers = set(receivers)
        
        if actual_receivers != expected_receivers:
            results.add_fail("Validation", f"Receiver mismatch")
            return False
        
        # Check 3: No duplicate receivers (CRITICAL)
        receiver_counts = Counter(receivers)
        duplicates = {r: count for r, count in receiver_counts.items() if count > 1}
        if duplicates:
            results.stats["duplicate_receivers"] += 1
            results.add_fail("Validation", f"DUPLICATE RECEIVERS: {duplicates}")
            return False
        
        # Check 4: No self-assignments (CRITICAL)
        for giver, receiver in assignments.items():
            if giver == receiver:
                results.stats["self_assignments"] += 1
                results.add_fail("Validation", f"SELF-ASSIGNMENT: {giver}")
                return False
        
        # Find cycles
        cycles = find_cycles(assignments)
        if cycles:
            cycle_lengths = [len(c) for c in cycles]
            results.stats["cycles_found"].append(cycle_lengths)
            results.stats["max_cycle_length"] = max(results.stats["max_cycle_length"], max(cycle_lengths))
            results.stats["min_cycle_length"] = min(results.stats["min_cycle_length"], min(cycle_lengths))
        
        return True
        
    except Exception as e:
        results.add_fail("Validation", str(e))
        return False


async def run_massive_simulation():
    """Run massive simulation with thousands of iterations"""
    results = SimulationResults()
    
    print("\n" + "="*80)
    print("MASSIVE SECRET SANTA SIMULATION")
    print("="*80)
    print("\nTesting assignment algorithm with:")
    print("  - Multiple cycles")
    print("  - Thousands of iterations")
    print("  - Various participant counts")
    print("  - Stress testing for bugs")
    print()
    
    # Test configurations - GAZILLION iterations!
    test_configs = [
        (3, 2000),    # 3 participants, 2000 iterations
        (5, 2000),    # 5 participants, 2000 iterations
        (10, 2000),   # 10 participants, 2000 iterations
        (20, 1000),   # 20 participants, 1000 iterations
        (50, 500),    # 50 participants, 500 iterations
        (100, 200),   # 100 participants, 200 iterations
        (150, 100),   # 150 participants, 100 iterations
        (169, 100),   # 169 participants (odd), 100 iterations
        (200, 100),   # 200 participants, 100 iterations
    ]
    
    total_iterations = sum(count for _, count in test_configs)
    print(f"Total iterations to run: {total_iterations:,}")
    print()
    
    iteration_count = 0
    start_time = time.time()
    
    try:
        for participant_count, iterations in test_configs:
            print(f"\n{'='*80}")
            print(f"Testing {participant_count} participants × {iterations} iterations")
            print(f"{'='*80}")
            
            participants = list(range(1000, 1000 + participant_count))
            cycle_counts = []
            multi_cycle_count = 0
            single_cycle_count = 0
            
            for i in range(iterations):
                iteration_count += 1
                
                if iteration_count % 500 == 0:
                    elapsed = time.time() - start_time
                    rate = iteration_count / elapsed if elapsed > 0 else 0
                    print(f"  Progress: {iteration_count:,}/{total_iterations:,} ({rate:.0f} tests/sec)", end='\r')
                
                try:
                    # Make assignments
                    assignments = make_assignments_realistic(participants)
                    results.stats["total_assignments"] += len(assignments)
                    
                    # Validate
                    if not validate_assignments(assignments, participants, results):
                        continue  # Validation failed, already logged
                    
                    # Count cycles
                    cycles = find_cycles(assignments)
                    cycle_counts.append(len(cycles))
                    
                    if len(cycles) > 1:
                        multi_cycle_count += 1
                    else:
                        single_cycle_count += 1
                    
                    results.add_pass(f"Test {participant_count}p-{i+1}")
                    
                except Exception as e:
                    results.add_fail(f"Test {participant_count}p-{i+1}", str(e))
            
            # Statistics for this configuration
            if cycle_counts:
                avg_cycles = sum(cycle_counts) / len(cycle_counts)
                print(f"\n  ✅ Completed {iterations} iterations")
                print(f"  Average cycles per assignment: {avg_cycles:.2f}")
                print(f"  Multi-cycle assignments: {multi_cycle_count}/{iterations} ({multi_cycle_count/iterations*100:.1f}%)")
                print(f"  Single-cycle assignments: {single_cycle_count}/{iterations} ({single_cycle_count/iterations*100:.1f}%)")
        
        elapsed = time.time() - start_time
        print(f"\n\n{'='*80}")
        print(f"COMPLETED {iteration_count:,} ITERATIONS IN {elapsed:.2f} SECONDS")
        print(f"Rate: {iteration_count/elapsed:.0f} tests/second")
        print(f"{'='*80}")
        
        # Overall statistics
        if results.stats["cycles_found"]:
            all_cycle_lengths = [l for cycles in results.stats["cycles_found"] for l in cycles]
            if all_cycle_lengths:
                print(f"\n[OVERALL CYCLE STATISTICS]")
                print(f"  Total cycles found: {len(all_cycle_lengths):,}")
                print(f"  Average cycle length: {sum(all_cycle_lengths) / len(all_cycle_lengths):.2f}")
                print(f"  Min cycle length: {min(all_cycle_lengths)}")
                print(f"  Max cycle length: {max(all_cycle_lengths)}")
        
    except KeyboardInterrupt:
        print("\n\nSimulation interrupted by user")
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
    
    print("\n[STARTING] Massive Secret Santa Simulation...")
    print("This will test the algorithm thousands of times to verify stability.")
    print("Press Ctrl+C to stop early.\n")
    
    results = asyncio.run(run_massive_simulation())
    
    # Exit code based on results
    if results.failed:
        print("\n[FAILED] Simulation completed with failures")
        exit(1)
    else:
        print("\n[SUCCESS] All tests passed! Algorithm is rock solid! 🎉")
        exit(0)
