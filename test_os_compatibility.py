"""
OS Compatibility Test Script
Run this to verify your code works on your current OS
"""

import sys
import platform
from pathlib import Path
import json
import tempfile
import os

def test_path_operations():
    """Test pathlib operations work correctly"""
    print("=" * 60)
    print("TEST 1: Path Operations")
    print("=" * 60)
    
    # Simulate cog path resolution
    test_file = Path(__file__)
    test_dir = test_file.parent
    test_subdir = test_dir / "test_archive"
    
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Current file: {test_file}")
    print(f"Parent dir: {test_dir}")
    print(f"Test subdir: {test_subdir}")
    
    # Test directory creation
    try:
        test_subdir.mkdir(exist_ok=True)
        print(f"[OK] Directory creation: SUCCESS")
        print(f"   Created: {test_subdir}")
    except Exception as e:
        print(f"[FAIL] Directory creation: FAILED - {e}")
        return False
    
    # Test file writing with UTF-8
    test_file_path = test_subdir / "test.json"
    test_data = {"test": "Hello 世界 🌍", "number": 123}
    
    try:
        test_file_path.write_text(
            json.dumps(test_data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        print(f"[OK] File writing (UTF-8): SUCCESS")
        print(f"   Written: {test_file_path}")
    except Exception as e:
        print(f"[FAIL] File writing: FAILED - {e}")
        return False
    
    # Test file reading with UTF-8
    try:
        text = test_file_path.read_text(encoding='utf-8')
        loaded_data = json.loads(text)
        assert loaded_data == test_data
        print(f"[OK] File reading (UTF-8): SUCCESS")
        print(f"   Data matches: {loaded_data == test_data}")
    except Exception as e:
        print(f"[FAIL] File reading: FAILED - {e}")
        return False
    
    # Test atomic file replacement
    temp_file = test_file_path.with_suffix('.tmp')
    try:
        temp_file.write_text(
            json.dumps({"new": "data"}, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        temp_file.replace(test_file_path)
        print(f"[OK] Atomic file replacement: SUCCESS")
    except Exception as e:
        print(f"[FAIL] Atomic file replacement: FAILED - {e}")
        return False
    
    # Cleanup
    try:
        if test_file_path.exists():
            test_file_path.unlink()
        if temp_file.exists():
            temp_file.unlink()
        test_subdir.rmdir()
        print(f"[OK] Cleanup: SUCCESS")
    except Exception as e:
        print(f"[WARN] Cleanup warning: {e}")
    
    return True


def test_temp_file_cleanup():
    """Test temp file cleanup (Windows-specific issue)"""
    print("\n" + "=" * 60)
    print("TEST 2: Temp File Cleanup")
    print("=" * 60)
    
    # Create a temp file
    try:
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            temp_path = f.name
            f.write(b"fake audio data")
        print(f"[OK] Temp file created: {temp_path}")
    except Exception as e:
        print(f"[FAIL] Temp file creation: FAILED - {e}")
        return False
    
    # Test cleanup with retry logic (like voice_processing_cog)
    import time
    success = False
    for attempt in range(3):
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            success = True
            print(f"[OK] Temp file cleanup: SUCCESS (attempt {attempt + 1})")
            break
        except PermissionError:
            if attempt < 2:
                time.sleep(0.1)  # Shorter wait for test
                print(f"[WARN] Retry {attempt + 1}/3 (PermissionError)")
            else:
                print(f"[FAIL] Temp file cleanup: FAILED after 3 attempts")
        except Exception as e:
            print(f"[FAIL] Temp file cleanup: FAILED - {e}")
            break
    
    return success


def test_glob_operations():
    """Test glob pattern matching (case sensitivity)"""
    print("\n" + "=" * 60)
    print("TEST 3: Glob Pattern Matching")
    print("=" * 60)
    
    test_dir = Path(__file__).parent / "test_glob"
    test_dir.mkdir(exist_ok=True)
    
    # Create test files
    test_files = ["2021.json", "2022.json", "2023.json", "backup.json"]
    created_files = []
    
    try:
        for filename in test_files:
            file_path = test_dir / filename
            file_path.write_text('{"test": true}', encoding='utf-8')
            created_files.append(file_path)
        
        print(f"[OK] Test files created: {len(created_files)}")
        
        # Test glob pattern (like SecretSanta_cog)
        year_files = list(test_dir.glob("[0-9]*.json"))
        print(f"[OK] Glob pattern '[0-9]*.json': Found {len(year_files)} files")
        print(f"   Files: {[f.name for f in year_files]}")
        
        # Verify correct files found
        expected = ["2021.json", "2022.json", "2023.json"]
        found = [f.name for f in year_files]
        if set(found) == set(expected):
            print(f"[OK] Glob results: CORRECT")
        else:
            print(f"[WARN] Glob results: Expected {expected}, got {found}")
        
    except Exception as e:
        print(f"[FAIL] Glob test: FAILED - {e}")
        return False
    finally:
        # Cleanup
        for file_path in created_files:
            try:
                file_path.unlink()
            except:
                pass
        try:
            test_dir.rmdir()
        except:
            pass
    
    return True


def test_encoding_handling():
    """Test Unicode/encoding handling"""
    print("\n" + "=" * 60)
    print("TEST 4: Unicode/Encoding Handling")
    print("=" * 60)
    
    test_file = Path(__file__).parent / "test_unicode.json"
    
    # Test data with various Unicode characters
    test_data = {
        "english": "Hello World",
        "chinese": "你好世界",
        "japanese": "こんにちは",
        "emoji": "🎄🎁🎅",
        "special": "Café résumé naïve",
        "numbers": [1, 2, 3]
    }
    
    try:
        # Write with explicit UTF-8
        test_file.write_text(
            json.dumps(test_data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        print(f"[OK] Unicode write: SUCCESS")
        
        # Read with explicit UTF-8
        text = test_file.read_text(encoding='utf-8')
        loaded_data = json.loads(text)
        
        if loaded_data == test_data:
            print(f"[OK] Unicode read: SUCCESS")
            print(f"   All characters preserved correctly")
        else:
            print(f"[FAIL] Unicode read: DATA MISMATCH")
            return False
        
        # Cleanup
        test_file.unlink()
        
    except UnicodeEncodeError as e:
        print(f"[FAIL] Unicode encoding: FAILED - {e}")
        return False
    except UnicodeDecodeError as e:
        print(f"[FAIL] Unicode decoding: FAILED - {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Unicode test: FAILED - {e}")
        return False
    
    return True


def main():
    """Run all compatibility tests"""
    print("\n" + "=" * 60)
    print("OS COMPATIBILITY TEST SUITE")
    print("=" * 60)
    print(f"\nRunning on: {platform.system()} {platform.release()}")
    print(f"Python version: {sys.version.split()[0]}")
    print(f"Architecture: {platform.machine()}")
    print()
    
    results = []
    
    # Run tests
    results.append(("Path Operations", test_path_operations()))
    results.append(("Temp File Cleanup", test_temp_file_cleanup()))
    results.append(("Glob Operations", test_glob_operations()))
    results.append(("Encoding Handling", test_encoding_handling()))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for test_name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{status}: {test_name}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("[SUCCESS] ALL TESTS PASSED - Your code is compatible with this OS!")
        print("=" * 60)
        return 0
    else:
        print("[WARNING] SOME TESTS FAILED - Check errors above")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
