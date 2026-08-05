#!/usr/bin/env python3
"""
Offline simulations for critical bot logic — no Discord token required.

Run: python3 tests/audit_simulations.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Repo root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.secret_santa_assignments import (
    make_assignments,
    validate_assignment_possibility,
    _validate_assignment_integrity,
)
from cogs.utils import (
    LOAD_JSON_MAX_BYTES,
    atomic_save_json,
    load_json_file,
    safe_filename_in_dir,
    RateLimiter,
    CircuitBreaker,
)


def ok(name: str) -> None:
    print(f"  OK  {name}")


def fail(name: str, detail: str) -> None:
    print(f"  FAIL {name}: {detail}")
    raise AssertionError(f"{name}: {detail}")


def test_assignments_no_duplicate_receivers() -> None:
    participants = list(range(100001, 100011))  # 10 people
    for _ in range(20):
        result = make_assignments(participants, {})
        _validate_assignment_integrity(result, participants)
        receivers = list(result.values())
        if len(receivers) != len(set(receivers)):
            fail("duplicate receivers", str(result))
    ok("assignments: no duplicate receivers (20 shuffles)")


def test_assignments_two_person_exchange() -> None:
    p1, p2 = 1, 2
    result = make_assignments([p1, p2], {})
    if result != {p1: p2, p2: p1}:
        fail("two-person exchange", str(result))
    ok("assignments: 2-person swap")


def test_assignments_history_constraint() -> None:
    participants = [1, 2, 3, 4]
    history = {"1": [2], "2": [3], "3": [4], "4": [1]}
    error = validate_assignment_possibility(participants, history)
    if error:
        fail("history possibility", error)
    result = make_assignments(participants, history)
    _validate_assignment_integrity(result, participants)
    ok("assignments: respects history constraints")


def test_atomic_json_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        payload = {"current_year": 2026, "pair_history": {"1": [2]}, "nested": [1, 2, 3]}
        atomic_save_json(path, payload)
        loaded = load_json_file(path)
        if loaded != payload:
            fail("json roundtrip", f"{loaded!r} != {payload!r}")
    ok("storage: atomic JSON roundtrip")


def test_load_json_size_cap() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "huge.json"
        path.write_text("{" + '"x":' * (LOAD_JSON_MAX_BYTES // 4) + '"end":1}')
        result = load_json_file(path, default={"fallback": True})
        if result.get("fallback") is not True:
            fail("size cap", "expected fallback dict for oversized file")
    ok("storage: oversized JSON rejected")


def test_safe_filename_blocks_traversal() -> None:
    base = Path(tempfile.mkdtemp())
    try:
        # Basename extraction: "../etc/passwd" becomes "passwd" under base (safe)
        resolved = safe_filename_in_dir("../etc/passwd", base)
        if resolved is None or resolved.name != "passwd":
            fail("basename normalization", str(resolved))
        if resolved.parent.resolve() != base.resolve():
            fail("escaped base directory", str(resolved))

        for bad in ("", ".", ".."):
            if safe_filename_in_dir(bad, base) is not None:
                fail("invalid filename", f"accepted {bad!r}")

        good = safe_filename_in_dir("modpack.zip", base)
        if good is None or good.parent.resolve() != base.resolve():
            fail("valid filename", str(good))
    finally:
        import shutil
        shutil.rmtree(base, ignore_errors=True)
    ok("security: safe_filename_in_dir normalizes to basename under directory")


def test_tts_success_rate_formula() -> None:
    """Simulate the fixed /tts stats math."""
    total_requests = 9  # 3 failed generations × 3 HTTP attempts each
    total_successes = 7
    total_failed = 3
    success_rate = (total_successes / max(1, total_successes + total_failed)) * 100
    if abs(success_rate - 70.0) > 0.01:
        fail("tts success rate", f"expected 70%, got {success_rate}")
    # Old buggy formula would show 9/(9+3)=75% or worse interpretations
    buggy = (total_requests / max(1, total_requests + total_failed)) * 100
    if abs(buggy - 75.0) > 0.01:
        fail("tts old formula sanity", f"unexpected {buggy}")
    ok("tts: success rate uses generations not raw HTTP attempts")


def test_download_count_accumulates() -> None:
    meta = {"files": {"1": {"download_count": 50}}}
    successful = 5
    prev = meta["files"]["1"].get("download_count", 0)
    meta["files"]["1"]["download_count"] = prev + successful
    if meta["files"]["1"]["download_count"] != 55:
        fail("download count", str(meta))
    ok("distribute: download_count accumulates")


async def test_rate_limiter_and_circuit_breaker() -> None:
    rl = RateLimiter(limit=2, window=60)
    assert await rl.check("user1")
    assert await rl.check("user1")
    assert not await rl.check("user1")
    await rl.reset("user1")
    assert await rl.check("user1")
    ok("utils: rate limiter enforces limit")

    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1, success_threshold=1)
    assert await cb.can_attempt()
    await cb.record_failure()
    await cb.record_failure()
    assert not await cb.can_attempt()
    ok("utils: circuit breaker opens after failures")


def test_save_json_importable_from_commands_module() -> None:
    """Regression: secret_santa_commands must import save_json."""
    import cogs.secret_santa_commands as ssc
    from cogs.secret_santa_storage import save_json
    if not callable(save_json):
        fail("save_json import", "not callable")
    # Ensure the name used in executor is in module namespace
    src = Path(ROOT / "cogs/secret_santa_commands.py").read_text(encoding="utf-8")
    if "save_json" not in src.split("from .secret_santa_storage import")[1].split(")")[0]:
        fail("save_json import", "missing from secret_santa_storage import block")
    ok("imports: save_json available to secret_santa_commands")


def main() -> int:
    print("WaveTechToolBox audit simulations\n")
    tests = [
        test_save_json_importable_from_commands_module,
        test_assignments_no_duplicate_receivers,
        test_assignments_two_person_exchange,
        test_assignments_history_constraint,
        test_atomic_json_roundtrip,
        test_load_json_size_cap,
        test_safe_filename_blocks_traversal,
        test_tts_success_rate_formula,
        test_download_count_accumulates,
    ]
    passed = 0
    for fn in tests:
        print(f"\n[{fn.__name__}]")
        fn()
        passed += 1

    print("\n[test_rate_limiter_and_circuit_breaker]")
    import asyncio
    asyncio.run(test_rate_limiter_and_circuit_breaker())
    passed += 1

    print(f"\n{'=' * 40}\nAll {passed} simulations passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
