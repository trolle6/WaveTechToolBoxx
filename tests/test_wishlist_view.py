"""
Wishlist View Test Suite

Tests the wishlist viewing functionality with various edge cases and scenarios
that could cause issues for different users.

Run: python tests/test_wishlist_view.py
"""

import sys
from pathlib import Path
from unittest.mock import Mock, AsyncMock, MagicMock
import asyncio

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class MockUser:
    """Mock Discord user"""
    def __init__(self, user_id: int, name: str = "TestUser"):
        self.id = user_id
        self.name = name
        self.display_name = name


class MockInteraction:
    """Mock Discord interaction"""
    def __init__(self, user_id: int, user_name: str = "TestUser"):
        self.author = MockUser(user_id, user_name)
        self.response = Mock()
        self.response.defer = AsyncMock()
        self.edit_original_response = AsyncMock()
        self.bot = Mock()
        
        
def create_mock_cog(state_data):
    """Create a mock SecretSantaCog with given state"""
    cog = Mock()
    cog.state = state_data
    cog._lock = asyncio.Lock()
    
    def _get_current_event():
        return state_data.get("current_event")
    
    cog._get_current_event = _get_current_event
    return cog


class TestWishlistViewScenarios:
    """Test various wishlist view scenarios"""
    
    async def _simulate_view_wishlist(self, user_id: int, state_data: dict):
        """
        Simulate the wishlist_view function logic
        Returns: (success, error_message, embed_data)
        """
        try:
            # Simulate participant_check decorator
            event = state_data.get("current_event")
            if not event or not event.get("active"):
                return False, "No active event or event not active", None
            
            if str(user_id) not in event.get("participants", {}):
                return False, "User not a participant", None
            
            # Simulate wishlist_view logic
            wishlists = event.get("wishlists", {})
            user_wishlist = wishlists.get(str(user_id), [])
            
            if not user_wishlist:
                embed_data = {
                    "title": "📋 Your Wishlist",
                    "description": "Your wishlist is empty! Add items with `/ss wishlist add`",
                    "empty": True
                }
            else:
                embed_data = {
                    "title": "📋 Your Wishlist",
                    "description": f"You have **{len(user_wishlist)}** item{'s' if len(user_wishlist) != 1 else ''} on your list",
                    "items": user_wishlist,
                    "empty": False
                }
            
            return True, None, embed_data
            
        except Exception as e:
            return False, f"Exception: {str(e)}", None
    
    async def test_1_user_with_items(self):
        """Test 1: User with wishlist items - NORMAL CASE"""
        print("\n[TEST 1] User with wishlist items (normal case)")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "12345": "Alice"
                },
                "wishlists": {
                    "12345": ["Gaming headset", "Coffee mug", "Book on Python"]
                }
            }
        }
        
        success, error, embed = await self._simulate_view_wishlist(12345, state)
        
        assert success, f"Should succeed but got error: {error}"
        assert embed is not None
        assert not embed["empty"]
        assert len(embed["items"]) == 3
        print("   [PASS] User can view their 3 items")
        return True
    
    async def test_2_user_with_empty_wishlist(self):
        """Test 2: User with empty wishlist - NORMAL CASE"""
        print("\n[TEST 2] User with empty wishlist")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "12345": "Bob"
                },
                "wishlists": {}  # No wishlist entry yet
            }
        }
        
        success, error, embed = await self._simulate_view_wishlist(12345, state)
        
        assert success, f"Should succeed but got error: {error}"
        assert embed is not None
        assert embed["empty"]
        print("   [PASS] User sees empty wishlist message")
        return True
    
    async def test_3_no_active_event(self):
        """Test 3: No active event - SHOULD FAIL"""
        print("\n[TEST 3] No active event")
        
        state = {
            "current_event": None
        }
        
        success, error, embed = await self._simulate_view_wishlist(12345, state)
        
        assert not success, "Should fail when no active event"
        assert "No active event" in error
        print(f"   [PASS] Correctly rejected: {error}")
        return True
    
    async def test_4_event_not_active(self):
        """Test 4: Event exists but not marked active - SHOULD FAIL"""
        print("\n[TEST 4] Event exists but not active")
        
        state = {
            "current_event": {
                "active": False,  # Not active!
                "participants": {
                    "12345": "Charlie"
                }
            }
        }
        
        success, error, embed = await self._simulate_view_wishlist(12345, state)
        
        assert not success, "Should fail when event not active"
        print(f"   [PASS] Correctly rejected: {error}")
        return True
    
    async def test_5_user_not_participant(self):
        """Test 5: User is not a participant - SHOULD FAIL"""
        print("\n[TEST 5] User not a participant")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "99999": "OtherUser"  # Different user
                },
                "wishlists": {}
            }
        }
        
        success, error, embed = await self._simulate_view_wishlist(12345, state)
        
        assert not success, "Should fail when user not participant"
        assert "not a participant" in error
        print(f"   [PASS] Correctly rejected: {error}")
        return True
    
    async def test_6_int_vs_string_id_mismatch(self):
        """Test 6: User ID stored as int instead of string - POTENTIAL BUG"""
        print("\n[TEST 6] Int vs String ID mismatch")
        
        # This could be a real issue - if IDs are stored inconsistently
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    12345: "Dave"  # Stored as int, not string!
                },
                "wishlists": {
                    "12345": ["Item 1"]  # Wishlist as string
                }
            }
        }
        
        success, error, embed = await self._simulate_view_wishlist(12345, state)
        
        # This will likely FAIL because str(12345) != 12345
        if not success:
            print(f"   [WARNING] POTENTIAL BUG FOUND: {error}")
            print("   This happens when user IDs are stored as integers instead of strings!")
            return "BUG_FOUND"
        else:
            print("   [PASS] Handled correctly")
            return True
    
    async def test_7_missing_wishlists_key(self):
        """Test 7: Event has no 'wishlists' key at all"""
        print("\n[TEST 7] Missing wishlists key in event")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "12345": "Eve"
                }
                # No 'wishlists' key!
            }
        }
        
        success, error, embed = await self._simulate_view_wishlist(12345, state)
        
        assert success, f"Should succeed with empty wishlist but got: {error}"
        assert embed["empty"]
        print("   [PASS] Handles missing wishlists key gracefully")
        return True
    
    async def test_8_special_characters_in_wishlist(self):
        """Test 8: Wishlist items with special characters"""
        print("\n[TEST 8] Special characters in wishlist items")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "12345": "Frank"
                },
                "wishlists": {
                    "12345": [
                        "Book: \"Python & You\"",
                        "50€ gift card",
                        "Tea ☕ set",
                        "Item with\nnewline",
                        "Very " + "long " * 100 + "item"
                    ]
                }
            }
        }
        
        success, error, embed = await self._simulate_view_wishlist(12345, state)
        
        assert success, f"Should succeed but got error: {error}"
        assert len(embed["items"]) == 5
        print("   [PASS] Handles special characters")
        return True
    
    async def test_9_concurrent_access(self):
        """Test 9: Multiple users viewing wishlist simultaneously"""
        print("\n[TEST 9] Concurrent access by multiple users")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "11111": "User1",
                    "22222": "User2",
                    "33333": "User3"
                },
                "wishlists": {
                    "11111": ["Item A"],
                    "22222": ["Item B"],
                    "33333": ["Item C"]
                }
            }
        }
        
        # Simulate 3 users viewing at the same time
        tasks = [
            self._simulate_view_wishlist(11111, state),
            self._simulate_view_wishlist(22222, state),
            self._simulate_view_wishlist(33333, state)
        ]
        
        results = await asyncio.gather(*tasks)
        
        for i, (success, error, embed) in enumerate(results, 1):
            assert success, f"User {i} failed: {error}"
            assert len(embed["items"]) == 1
        
        print("   [PASS] All 3 users accessed successfully")
        return True
    
    async def test_10_empty_string_vs_empty_list(self):
        """Test 10: Wishlist is empty string instead of empty list"""
        print("\n[TEST 10] Wishlist stored as empty string (type mismatch)")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "12345": "Grace"
                },
                "wishlists": {
                    "12345": ""  # Empty string instead of list!
                }
            }
        }
        
        try:
            success, error, embed = await self._simulate_view_wishlist(12345, state)
            
            # The empty string will be falsy, so should show as empty
            if success and embed["empty"]:
                print("   [PASS] Handles empty string gracefully")
                return True
            else:
                print(f"   [WARNING] UNEXPECTED: success={success}, error={error}")
                return "UNEXPECTED"
        except Exception as e:
            print(f"   [WARNING] POTENTIAL BUG: Exception raised: {e}")
            return "BUG_FOUND"


class TestViewGifteeWishlist:
    """Test viewing giftee's wishlist"""
    
    async def _simulate_view_giftee_wishlist(self, user_id: int, state_data: dict):
        """Simulate viewing giftee's wishlist"""
        try:
            # Participant check
            event = state_data.get("current_event")
            if not event or not event.get("active"):
                return False, "No active event", None
            
            if str(user_id) not in event.get("participants", {}):
                return False, "Not a participant", None
            
            # Check assignment
            if str(user_id) not in event.get("assignments", {}):
                return False, "No assignment yet", None
            
            receiver_id = str(event["assignments"][str(user_id)])
            receiver_name = event["participants"].get(receiver_id, f"User {receiver_id}")
            
            wishlists = event.get("wishlists", {})
            giftee_wishlist = wishlists.get(receiver_id, [])
            
            embed_data = {
                "receiver_name": receiver_name,
                "items": giftee_wishlist,
                "empty": len(giftee_wishlist) == 0
            }
            
            return True, None, embed_data
            
        except Exception as e:
            return False, f"Exception: {str(e)}", None
    
    async def test_1_giftee_has_wishlist(self):
        """Test 1: Giftee has wishlist items"""
        print("\n[GIFTEE TEST 1] Giftee has wishlist")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "111": "Alice",
                    "222": "Bob"
                },
                "assignments": {
                    "111": "222"  # Alice gives to Bob
                },
                "wishlists": {
                    "222": ["Gaming mouse", "Keyboard"]
                }
            }
        }
        
        success, error, embed = await self._simulate_view_giftee_wishlist(111, state)
        
        assert success, f"Should succeed but got: {error}"
        assert embed["receiver_name"] == "Bob"
        assert len(embed["items"]) == 2
        print("   [PASS] Alice can see Bob's wishlist")
        return True
    
    async def test_2_giftee_empty_wishlist(self):
        """Test 2: Giftee has no wishlist"""
        print("\n[GIFTEE TEST 2] Giftee has empty wishlist")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "111": "Alice",
                    "222": "Bob"
                },
                "assignments": {
                    "111": "222"
                },
                "wishlists": {}  # Bob has no wishlist
            }
        }
        
        success, error, embed = await self._simulate_view_giftee_wishlist(111, state)
        
        assert success, f"Should succeed but got: {error}"
        assert embed["empty"]
        print("   [PASS] Shows empty wishlist message")
        return True
    
    async def test_3_no_assignment_yet(self):
        """Test 3: User has no assignment yet - SHOULD FAIL"""
        print("\n[GIFTEE TEST 3] No assignment yet")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "111": "Alice",
                    "222": "Bob"
                },
                "assignments": {}  # No assignments yet
            }
        }
        
        success, error, embed = await self._simulate_view_giftee_wishlist(111, state)
        
        assert not success, "Should fail with no assignment"
        assert "No assignment" in error
        print(f"   [PASS] Correctly rejected: {error}")
        return True
    
    async def test_4_assignment_id_mismatch(self):
        """Test 4: Assignment uses int ID but participants use string"""
        print("\n[GIFTEE TEST 4] Assignment ID type mismatch")
        
        state = {
            "current_event": {
                "active": True,
                "participants": {
                    "111": "Alice",
                    "222": "Bob"
                },
                "assignments": {
                    "111": 222  # Int instead of string!
                },
                "wishlists": {
                    "222": ["Item"]
                }
            }
        }
        
        success, error, embed = await self._simulate_view_giftee_wishlist(111, state)
        
        # This might work since we convert to string, but let's verify
        if success:
            # Check if receiver_name is correct or shows "User 222"
            if "User 222" in embed["receiver_name"]:
                print("   [WARNING] Name lookup failed due to type mismatch")
                return "WARNING"
            else:
                print("   [PASS] Handled type conversion")
                return True
        else:
            print(f"   [WARNING] FAILED: {error}")
            return "BUG_FOUND"


async def run_all_tests():
    """Run all wishlist view tests"""
    print("=" * 70)
    print("WISHLIST VIEW SIMULATION TEST SUITE")
    print("=" * 70)
    print("\nTesting various scenarios that might cause 'view wishlist' to fail...")
    
    # Test own wishlist viewing
    print("\n" + "=" * 70)
    print("PART A: View Own Wishlist")
    print("=" * 70)
    
    test_suite_1 = TestWishlistViewScenarios()
    test_methods_1 = [
        test_suite_1.test_1_user_with_items,
        test_suite_1.test_2_user_with_empty_wishlist,
        test_suite_1.test_3_no_active_event,
        test_suite_1.test_4_event_not_active,
        test_suite_1.test_5_user_not_participant,
        test_suite_1.test_6_int_vs_string_id_mismatch,
        test_suite_1.test_7_missing_wishlists_key,
        test_suite_1.test_8_special_characters_in_wishlist,
        test_suite_1.test_9_concurrent_access,
        test_suite_1.test_10_empty_string_vs_empty_list
    ]
    
    results_1 = []
    bugs_found = []
    
    for test_method in test_methods_1:
        try:
            result = await test_method()
            results_1.append((test_method.__name__, "PASSED", None))
            if result == "BUG_FOUND":
                bugs_found.append(test_method.__name__)
            elif result == "WARNING":
                bugs_found.append(f"{test_method.__name__} (WARNING)")
        except AssertionError as e:
            results_1.append((test_method.__name__, "FAILED", str(e)))
            print(f"   [FAIL] {e}")
        except Exception as e:
            results_1.append((test_method.__name__, "ERROR", str(e)))
            print(f"   [ERROR] {e}")
    
    # Test giftee wishlist viewing
    print("\n" + "=" * 70)
    print("PART B: View Giftee's Wishlist")
    print("=" * 70)
    
    test_suite_2 = TestViewGifteeWishlist()
    test_methods_2 = [
        test_suite_2.test_1_giftee_has_wishlist,
        test_suite_2.test_2_giftee_empty_wishlist,
        test_suite_2.test_3_no_assignment_yet,
        test_suite_2.test_4_assignment_id_mismatch
    ]
    
    results_2 = []
    
    for test_method in test_methods_2:
        try:
            result = await test_method()
            results_2.append((test_method.__name__, "PASSED", None))
            if result == "BUG_FOUND":
                bugs_found.append(test_method.__name__)
            elif result == "WARNING":
                bugs_found.append(f"{test_method.__name__} (WARNING)")
        except AssertionError as e:
            results_2.append((test_method.__name__, "FAILED", str(e)))
            print(f"   [FAIL] {e}")
        except Exception as e:
            results_2.append((test_method.__name__, "ERROR", str(e)))
            print(f"   [ERROR] {e}")
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    all_results = results_1 + results_2
    passed = sum(1 for _, status, _ in all_results if status == "PASSED")
    failed = sum(1 for _, status, _ in all_results if status == "FAILED")
    errors = sum(1 for _, status, _ in all_results if status == "ERROR")
    
    print(f"\nTotal Tests: {len(all_results)}")
    print(f"[+] Passed: {passed}")
    print(f"[-] Failed: {failed}")
    print(f"[!] Errors: {errors}")
    
    if bugs_found:
        print(f"\n[!] POTENTIAL BUGS DETECTED: {len(bugs_found)}")
        for bug in bugs_found:
            print(f"   - {bug}")
    
    if failed > 0 or errors > 0:
        print("\n[-] Some tests failed:")
        for name, status, error in all_results:
            if status in ["FAILED", "ERROR"]:
                print(f"   - {name}: {error}")
    
    if passed == len(all_results) and not bugs_found:
        print("\n[+] ALL TESTS PASSED - No issues detected!")
    elif bugs_found:
        print("\n[!] Tests completed but potential bugs were identified above")
    
    print("=" * 70)
    
    return bugs_found, failed, errors


if __name__ == "__main__":
    bugs, failed, errors = asyncio.run(run_all_tests())
    
    # Exit with appropriate code
    if failed > 0 or errors > 0:
        sys.exit(1)
    elif bugs:
        sys.exit(2)  # Exit code 2 for warnings/bugs
    else:
        sys.exit(0)

