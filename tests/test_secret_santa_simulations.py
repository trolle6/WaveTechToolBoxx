"""
Secret Santa Simulation Test Suite

Tests the Secret Santa assignment algorithm with various scenarios:
- Small groups (2-5 people)
- Medium groups (10-20 people)
- Large groups (50+ people)
- Edge cases (impossible assignments, history conflicts)
- Concurrency stress tests

Run: python -m pytest tests/test_secret_santa_simulations.py -v
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from cogs.SecretSanta_cog import make_assignments, validate_assignment_possibility


class TestSmallGroups:
    """Test Secret Santa with small groups (2-5 people)"""
    
    def test_two_people_first_year(self):
        """Test 2 people, no history"""
        participants = [100, 200]
        history = {}
        
        assignments = make_assignments(participants, history)
        
        assert len(assignments) == 2
        assert assignments[100] == 200
        assert assignments[200] == 100
        assert str(100) in history
        assert str(200) in history
        print("✅ 2 people, first year: PASSED")
    
    def test_two_people_impossible(self):
        """Test 2 people who have already paired - should fail"""
        participants = [100, 200]
        history = {
            "100": [200],
            "200": [100]
        }
        
        try:
            assignments = make_assignments(participants, history)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "2-person assignment failed" in str(e)
            print("✅ 2 people, impossible pairing: PASSED (correctly failed)")
    
    def test_three_people_no_history(self):
        """Test 3 people, no history - should form chain"""
        participants = [100, 200, 300]
        history = {}
        
        assignments = make_assignments(participants, history)
        
        assert len(assignments) == 3
        # All should give to someone different
        for giver, receiver in assignments.items():
            assert giver != receiver
        # No two people should give to same person
        receivers = list(assignments.values())
        assert len(set(receivers)) == 3
        print("✅ 3 people, no history: PASSED")
    
    def test_five_people_multiple_years(self):
        """Test 5 people over 3 years"""
        participants = [100, 200, 300, 400, 500]
        history = {}
        
        # Year 1
        assignments_y1 = make_assignments(participants, history)
        assert len(assignments_y1) == 5
        
        # Year 2 - should avoid year 1 pairings
        assignments_y2 = make_assignments(participants, history)
        assert len(assignments_y2) == 5
        # Verify different assignments
        different = sum(1 for g in assignments_y1 if assignments_y1[g] != assignments_y2.get(g))
        assert different > 0, "Should have at least some different assignments"
        
        # Year 3
        assignments_y3 = make_assignments(participants, history)
        assert len(assignments_y3) == 5
        
        print(f"✅ 5 people, 3 years: PASSED")
        print(f"   Year 1: {assignments_y1}")
        print(f"   Year 2: {assignments_y2}")
        print(f"   Year 3: {assignments_y3}")


class TestMediumGroups:
    """Test Secret Santa with medium groups (10-20 people)"""
    
    def test_ten_people_five_years(self):
        """Test 10 people over 5 years"""
        participants = list(range(100, 110))  # 100-109
        history = {}
        
        all_assignments = []
        
        for year in range(5):
            assignments = make_assignments(participants, history)
            assert len(assignments) == 10
            
            # Verify integrity
            receivers = list(assignments.values())
            assert len(set(receivers)) == 10, "Each person should receive from exactly one person"
            
            # Verify no self-assignments
            for giver, receiver in assignments.items():
                assert giver != receiver, "No one should give to themselves"
            
            all_assignments.append(assignments)
            
        print(f"✅ 10 people, 5 years: PASSED")
        for i, assignments in enumerate(all_assignments, 1):
            print(f"   Year {i}: {len(assignments)} assignments")
    
    def test_twenty_people_stress(self):
        """Test 20 people - stress test"""
        participants = list(range(100, 120))  # 100-119
        history = {}
        
        # Run multiple years
        for year in range(10):
            assignments = make_assignments(participants, history)
            assert len(assignments) == 20
            
            # Verify all participants assigned
            assert set(assignments.keys()) == set(participants)
            
        print(f"✅ 20 people, 10 years: PASSED")


class TestEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_impossible_assignment(self):
        """Test truly impossible assignment (everyone paired with everyone)"""
        participants = [100, 200, 300]
        
        # Create complete history (everyone gave to everyone)
        history = {
            "100": [200, 300],
            "200": [100, 300],
            "300": [100, 200]
        }
        
        # This should fail validation
        error = validate_assignment_possibility(participants, history)
        assert error is not None
        assert "impossible" in error.lower()
        print(f"✅ Impossible assignment detected: PASSED")
    
    def test_single_participant(self):
        """Test with only 1 participant - should fail"""
        participants = [100]
        history = {}
        
        try:
            assignments = make_assignments(participants, history)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "at least 2 participants" in str(e).lower()
            print("✅ Single participant rejected: PASSED")
    
    def test_empty_participants(self):
        """Test with no participants - should fail"""
        participants = []
        history = {}
        
        try:
            assignments = make_assignments(participants, history)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "at least 2 participants" in str(e).lower()
            print("✅ Empty participants rejected: PASSED")


class TestConcurrency:
    """Test concurrent assignment generation"""
    
    async def test_concurrent_assignments(self):
        """Test multiple concurrent assignment requests"""
        participants = list(range(100, 120))  # 20 people
        
        async def create_assignment(year_id):
            """Create an assignment for a specific year"""
            history = {}  # Each gets fresh history for independence
            try:
                assignments = make_assignments(participants, history)
                return year_id, assignments, None
            except Exception as e:
                return year_id, None, e
        
        # Run 10 concurrent assignment generations
        tasks = [create_assignment(i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        
        # All should succeed
        for year_id, assignments, error in results:
            assert error is None, f"Year {year_id} failed: {error}"
            assert assignments is not None
            assert len(assignments) == len(participants)
        
        print(f"✅ 10 concurrent assignments: PASSED")
    
    def test_concurrent_sync(self):
        """Run async concurrency test in sync context"""
        asyncio.run(self.test_concurrent_assignments())


class TestLargeGroups:
    """Test with large participant counts"""
    
    def test_fifty_people(self):
        """Test 50 people"""
        participants = list(range(100, 150))
        history = {}
        
        assignments = make_assignments(participants, history)
        assert len(assignments) == 50
        
        # Verify integrity
        receivers = list(assignments.values())
        assert len(set(receivers)) == 50
        
        print(f"✅ 50 people: PASSED")
    
    def test_hundred_people(self):
        """Test 100 people - extreme stress test"""
        participants = list(range(100, 200))
        history = {}
        
        assignments = make_assignments(participants, history)
        assert len(assignments) == 100
        
        # Verify integrity
        receivers = list(assignments.values())
        assert len(set(receivers)) == 100
        
        print(f"✅ 100 people: PASSED")


class TestHistoryConstraints:
    """Test history constraint handling"""
    
    def test_partial_history(self):
        """Test with partial history (some people have history, some don't)"""
        participants = [100, 200, 300, 400, 500]
        
        # Only person 100 has history
        history = {
            "100": [200, 300]  # 100 gave to 200 and 300 before
        }
        
        assignments = make_assignments(participants, history)
        
        # Person 100 should NOT give to 200 or 300
        assert assignments[100] not in [200, 300]
        assert assignments[100] in [400, 500]
        
        print(f"✅ Partial history respected: PASSED")
    
    def test_complex_history_web(self):
        """Test with complex history web"""
        participants = [100, 200, 300, 400, 500]
        
        # Create complex web of past assignments
        history = {
            "100": [200],
            "200": [300],
            "300": [400],
            "400": [500],
            "500": [100]
        }
        
        assignments = make_assignments(participants, history)
        
        # Verify history is respected
        for giver, past_receivers in history.items():
            current_receiver = assignments[int(giver)]
            assert current_receiver not in past_receivers
        
        print(f"✅ Complex history web respected: PASSED")


class TestValidationFunction:
    """Test the validation function separately"""
    
    def test_validation_accepts_possible(self):
        """Test validation accepts possible assignments"""
        participants = [100, 200, 300, 400, 500]
        history = {
            "100": [200]  # Only one past pairing
        }
        
        error = validate_assignment_possibility(participants, history)
        assert error is None
        print(f"✅ Validation accepts possible: PASSED")
    
    def test_validation_rejects_impossible(self):
        """Test validation rejects impossible assignments"""
        participants = [100, 200]
        history = {
            "100": [200],
            "200": [100]
        }
        
        error = validate_assignment_possibility(participants, history)
        assert error is not None
        assert "impossible" in error.lower()
        print(f"✅ Validation rejects impossible: PASSED")


def run_all_tests():
    """Run all test classes"""
    print("=" * 70)
    print("SECRET SANTA SIMULATION TEST SUITE")
    print("=" * 70)
    
    test_classes = [
        TestSmallGroups(),
        TestMediumGroups(),
        TestEdgeCases(),
        TestConcurrency(),
        TestLargeGroups(),
        TestHistoryConstraints(),
        TestValidationFunction()
    ]
    
    total_tests = 0
    passed_tests = 0
    failed_tests = []
    
    for test_class in test_classes:
        print(f"\n📦 Running: {test_class.__class__.__name__}")
        print("-" * 70)
        
        # Get all test methods
        test_methods = [m for m in dir(test_class) if m.startswith('test_')]
        
        for method_name in test_methods:
            total_tests += 1
            try:
                method = getattr(test_class, method_name)
                method()
                passed_tests += 1
            except Exception as e:
                failed_tests.append((test_class.__class__.__name__, method_name, e))
                print(f"❌ {method_name}: FAILED - {e}")
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Total Tests: {total_tests}")
    print(f"✅ Passed: {passed_tests}")
    print(f"❌ Failed: {len(failed_tests)}")
    
    if failed_tests:
        print("\nFailed Tests:")
        for class_name, method_name, error in failed_tests:
            print(f"  - {class_name}.{method_name}: {error}")
    else:
        print("\n🎉 ALL TESTS PASSED! 🎉")
    
    print("=" * 70)
    
    return len(failed_tests) == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

