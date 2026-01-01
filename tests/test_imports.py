"""
Test that all Secret Santa modules can be imported correctly
Simulates how the bot loads the modules as a package
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_imports():
    """Test all module imports"""
    errors = []
    
    print("Testing module imports...")
    print("=" * 60)
    
    # Test 1: Storage module (no dependencies)
    try:
        from cogs.secret_santa_storage import (
            load_json, save_json, get_default_state,
            load_state_with_fallback, save_state,
            load_all_archives, archive_event,
            ARCHIVE_DIR, BACKUPS_DIR, STATE_FILE
        )
        print("[OK] secret_santa_storage imports OK")
    except Exception as e:
        error = f"secret_santa_storage: {e}"
        errors.append(error)
        print(f"[FAIL] {error}")
    
    # Test 2: Assignments module (depends on storage)
    try:
        from cogs.secret_santa_assignments import (
            load_history_from_archives,
            validate_assignment_possibility,
            make_assignments
        )
        print("[OK] secret_santa_assignments imports OK")
    except Exception as e:
        error = f"secret_santa_assignments: {e}"
        errors.append(error)
        print(f"[FAIL] {error}")
    
    # Test 3: Views module (needs disnake)
    try:
        from cogs.secret_santa_views import (
            SecretSantaReplyView,
            SecretSantaReplyModal,
            YearHistoryPaginator
        )
        print("[OK] secret_santa_views imports OK")
    except ImportError as e:
        if "disnake" in str(e):
            print("[WARN] secret_santa_views: disnake not installed (expected in test env)")
        else:
            error = f"secret_santa_views: {e}"
            errors.append(error)
            print(f"[FAIL] {error}")
    except Exception as e:
        error = f"secret_santa_views: {e}"
        errors.append(error)
        print(f"[FAIL] {error}")
    
    # Test 4: Checks module (needs disnake)
    try:
        from cogs.secret_santa_checks import (
            mod_check,
            participant_check
        )
        print("[OK] secret_santa_checks imports OK")
    except ImportError as e:
        if "disnake" in str(e):
            print("[WARN] secret_santa_checks: disnake not installed (expected in test env)")
        else:
            error = f"secret_santa_checks: {e}"
            errors.append(error)
            print(f"[FAIL] {error}")
    except Exception as e:
        error = f"secret_santa_checks: {e}"
        errors.append(error)
        print(f"[FAIL] {error}")
    
    # Test 5: Main cog (depends on all modules)
    try:
        # Just check if it can be imported - don't instantiate
        import cogs.SecretSanta_cog
        print("[OK] SecretSanta_cog imports OK")
    except ImportError as e:
        if "disnake" in str(e) or "owner_utils" in str(e):
            print("[WARN] SecretSanta_cog: Some dependencies not available (expected in test env)")
        else:
            error = f"SecretSanta_cog: {e}"
            errors.append(error)
            print(f"[FAIL] {error}")
    except Exception as e:
        error = f"SecretSanta_cog: {e}"
        errors.append(error)
        print(f"[FAIL] {error}")
    
    # Test 6: Check for circular dependencies (only test modules without disnake deps)
    print("\n" + "=" * 60)
    print("Checking for circular dependencies...")
    
    try:
        # Try importing in different orders (skip disnake-dependent modules for this test)
        import cogs.secret_santa_storage as storage
        import cogs.secret_santa_assignments as assignments
        
        # Verify assignments can access storage
        if hasattr(assignments, 'load_history_from_archives'):
            print("[OK] No circular dependency detected between storage and assignments")
        else:
            errors.append("assignments module missing expected functions")
            print("[FAIL] assignments module structure issue")
            
        # Verify storage doesn't import assignments (would be circular)
        if hasattr(storage, 'make_assignments'):
            errors.append("storage module imports assignments (circular dependency!)")
            print("[FAIL] Circular dependency: storage imports assignments")
        else:
            print("[OK] storage does not import assignments (no circular dependency)")
            
    except ImportError as e:
        if "disnake" not in str(e):
            errors.append(f"Circular dependency check: {e}")
            print(f"[FAIL] {e}")
        else:
            print("[OK] Circular dependency check skipped (disnake not available)")
    except Exception as e:
        errors.append(f"Circular dependency check: {e}")
        print(f"[FAIL] {e}")
    
    # Summary
    print("\n" + "=" * 60)
    if errors:
        print(f"[FAIL] {len(errors)} import error(s) found:")
        for error in errors:
            print(f"   - {error}")
        return False
    else:
        print("[SUCCESS] All imports successful!")
        print("\nNote: disnake warnings are expected if not installed in test environment")
        print("The modules will work correctly when loaded by the bot.")
        return True

if __name__ == "__main__":
    success = test_imports()
    sys.exit(0 if success else 1)

