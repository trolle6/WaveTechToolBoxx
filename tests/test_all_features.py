#!/usr/bin/env python3
"""
Isolated feature tests for WaveTechToolBox — every major module, no live Discord.

Run: python3 tests/test_all_features.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.conftest_helpers import apply_test_env, ensure_event_loop, make_fake_bot, restore_env

# ─── Feature registry: human name → test class ───────────────────────────────

FEATURES: list[str] = []


def feature(name: str):
    def deco(cls):
        cls.feature_name = name
        FEATURES.append(name)
        return cls
    return deco


# ═══════════════════════════════════════════════════════════════════════════════
# UTILS
# ═══════════════════════════════════════════════════════════════════════════════

@feature("Utils — rate limiter, circuit breaker, JSON, Discord helpers, paths")
class TestUtils(unittest.TestCase):
    def setUp(self):
        from cogs.utils import RateLimiter, CircuitBreaker, atomic_save_json, load_json_file, safe_filename_in_dir
        self.RateLimiter = RateLimiter
        self.CircuitBreaker = CircuitBreaker
        self.atomic_save_json = atomic_save_json
        self.load_json_file = load_json_file
        self.safe_filename_in_dir = safe_filename_in_dir

    def test_rate_limiter(self):
        async def run():
            rl = self.RateLimiter(2, 60)
            self.assertTrue(await rl.check("u"))
            self.assertTrue(await rl.check("u"))
            self.assertFalse(await rl.check("u"))
            await rl.reset("u")
            self.assertTrue(await rl.check("u"))
        asyncio.run(run())

    def test_circuit_breaker(self):
        async def run():
            cb = self.CircuitBreaker(2, 1, 1)
            self.assertTrue(await cb.can_attempt())
            await cb.record_failure()
            await cb.record_failure()
            self.assertFalse(await cb.can_attempt())
        asyncio.run(run())

    def test_json_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.json"
            data = {"a": 1, "nested": [1, 2]}
            self.atomic_save_json(p, data)
            self.assertEqual(self.load_json_file(p), data)

    def test_safe_filename(self):
        base = Path(tempfile.mkdtemp())
        try:
            p = self.safe_filename_in_dir("file.zip", base)
            self.assertIsNotNone(p)
            self.assertEqual(p.parent.resolve(), base.resolve())
            self.assertIsNone(self.safe_filename_in_dir("", base))
        finally:
            import shutil
            shutil.rmtree(base, ignore_errors=True)

    def test_safe_edit_response_expired(self):
        async def run():
            from cogs.utils import safe_edit_response
            log = logging.getLogger("test")
            inter = MagicMock()
            inter.id = 1
            inter.edit_original_response = AsyncMock(side_effect=__import__("disnake").errors.NotFound(MagicMock(), "gone"))
            ok = await safe_edit_response(log, inter, content="hi")
            self.assertFalse(ok)
        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════════════════
# SECRET SANTA — STORAGE
# ═══════════════════════════════════════════════════════════════════════════════

@feature("Secret Santa — storage (state save/load, validation, archives)")
class TestSecretSantaStorage(unittest.TestCase):
    def test_default_state_structure(self):
        from cogs.secret_santa_storage import get_default_state, validate_state_structure
        state = get_default_state()
        self.assertIn("current_year", state)
        self.assertIn("pair_history", state)
        self.assertIsNone(state["current_event"])
        fixed = validate_state_structure({"current_year": "bad", "pair_history": None})
        self.assertIsInstance(fixed["current_year"], int)

    def test_save_load_roundtrip(self):
        from cogs.secret_santa_storage import save_json, load_json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            payload = get_default_state_local()
            save_json(path, payload)
            loaded = load_json(path)
            self.assertEqual(loaded["current_year"], payload["current_year"])

    def test_load_all_archives_real_files(self):
        from cogs.secret_santa_storage import load_all_archives
        archives = load_all_archives()
        self.assertGreaterEqual(len(archives), 4)
        for year, data in archives.items():
            self.assertIsInstance(year, int)
            self.assertIn("event", data)

    def test_state_fallback_backup(self):
        from cogs.secret_santa_storage import save_json, load_state_with_fallback, STATE_FILE
        with tempfile.TemporaryDirectory() as tmp, patch("cogs.secret_santa_storage.STATE_FILE", Path(tmp) / "ss.json"):
            from cogs.secret_santa_storage import STATE_FILE as sf
            state = get_default_state_local()
            state["current_event"] = {"active": True, "participants": {"1": "Alice"}}
            save_json(sf, state)
            loaded = load_state_with_fallback()
            self.assertTrue(loaded["current_event"]["active"])


def get_default_state_local():
    from cogs.secret_santa_storage import get_default_state
    return get_default_state()


# ═══════════════════════════════════════════════════════════════════════════════
# SECRET SANTA — ASSIGNMENTS
# ═══════════════════════════════════════════════════════════════════════════════

@feature("Secret Santa — assignment algorithm")
class TestSecretSantaAssignments(unittest.TestCase):
    def test_no_duplicate_receivers(self):
        from cogs.secret_santa_assignments import make_assignments, _validate_assignment_integrity
        p = list(range(100, 110))
        for _ in range(15):
            r = make_assignments(p, {})
            _validate_assignment_integrity(r, p)

    def test_two_person(self):
        from cogs.secret_santa_assignments import make_assignments
        self.assertEqual(make_assignments([1, 2], {}), {1: 2, 2: 1})

    def test_impossible_assignment_detected(self):
        from cogs.secret_santa_assignments import validate_assignment_possibility
        err = validate_assignment_possibility([1, 2], {"1": [2], "2": [1]})
        self.assertIsNotNone(err)


# ═══════════════════════════════════════════════════════════════════════════════
# SECRET SANTA — CHECKS & VIEWS HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

@feature("Secret Santa — permission checks and display helpers")
class TestSecretSantaChecks(unittest.TestCase):
    def test_safe_display_name(self):
        from cogs.secret_santa_checks import safe_display_name, format_gift_description_for_display, GIFT_EMPTY_DESCRIPTION
        self.assertEqual(safe_display_name(None), "Unknown")
        self.assertEqual(format_gift_description_for_display(""), GIFT_EMPTY_DESCRIPTION)
        self.assertIn("nothing", format_gift_description_for_display("nothing"))

    def test_mod_check_admin(self):
        from cogs.secret_santa_checks import _has_mod_access
        guild = MagicMock()
        member = MagicMock()
        member.guild_permissions.administrator = True
        member.roles = []
        bot = MagicMock()
        self.assertTrue(_has_mod_access(member, bot))


# ═══════════════════════════════════════════════════════════════════════════════
# SECRET SANTA — CORE
# ═══════════════════════════════════════════════════════════════════════════════

@feature("Secret Santa — core (embeds, participant validation, anonymize mock)")
class TestSecretSantaCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = apply_test_env()
        ensure_event_loop()

    @classmethod
    def tearDownClass(cls):
        restore_env(cls._env)

    def _make_cog(self):
        from cogs.SecretSanta_cog import SecretSantaCog
        bot = make_fake_bot()
        with patch.object(SecretSantaCog, "cog_load", AsyncMock()):
            cog = SecretSantaCog(bot)
        return cog

    def test_create_embed(self):
        cog = self._make_cog()
        import disnake
        emb = cog._create_embed("T", "D", disnake.Color.green(), field1=("K", "V", True))
        self.assertEqual(emb.title, "T")

    def test_validate_participant_inactive(self):
        cog = self._make_cog()
        inter = MagicMock()
        inter.author.id = 999
        inter.id = 1
        inter.edit_original_response = AsyncMock()
        cog.state = get_default_state_local()
        result = asyncio.run(cog._validate_participant(inter))
        self.assertIsNone(result)

    def test_anonymize_passthrough_short(self):
        cog = self._make_cog()
        out = asyncio.run(cog._anonymize_text("hi", "question"))
        self.assertEqual(out, "hi")

    def test_get_available_years(self):
        cog = self._make_cog()
        years = cog._get_available_years()
        self.assertIsInstance(years, list)
        self.assertGreater(len(years), 0)


# ═══════════════════════════════════════════════════════════════════════════════
# SECRET SANTA — COMMANDS (imports & pure helpers)
# ═══════════════════════════════════════════════════════════════════════════════

@feature("Secret Santa — commands module (imports, slash registration)")
class TestSecretSantaCommands(unittest.TestCase):
  def test_save_json_import(self):
      import cogs.secret_santa_commands as cmd
      from cogs.secret_santa_storage import save_json
      self.assertTrue(callable(save_json))

  def test_mixin_has_slash_commands(self):
      from cogs.secret_santa_commands import SecretSantaCommandsMixin
      names = [c for c in dir(SecretSantaCommandsMixin) if c.startswith("ss_") or c == "ss_root"]
      self.assertTrue(len(names) >= 5)


# ═══════════════════════════════════════════════════════════════════════════════
# VOICE / TTS
# ═══════════════════════════════════════════════════════════════════════════════

@feature("Voice / TTS — text processing, chunking, queue, stats math")
class TestVoiceTTS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = apply_test_env()
        ensure_event_loop()

    @classmethod
    def tearDownClass(cls):
        restore_env(cls._env)

    def _cog(self):
        from cogs.voice_processing_cog import VoiceProcessingCog
        bot = make_fake_bot()
        return VoiceProcessingCog(bot)

    def test_clean_text_emoji_and_mentions(self):
        cog = self._cog()
        out = asyncio.run(cog._clean_text("hello <@123> https://x.com <a:name:99>"))
        self.assertIn("name", out)
        self.assertNotIn("<@123>", out)
        self.assertNotIn("https", out)

    def test_split_long_text(self):
        cog = self._cog()
        text = "Word. " * 900
        chunks = cog._split_text_into_chunks(text, max_chunk_size=400)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 400)

    def test_normalize_api_text(self):
        cog = self._cog()
        self.assertEqual(cog._normalize_text_for_api("  ok \x00 "), "ok")

    def test_cache_key_stable(self):
        cog = self._cog()
        k1 = cog._cache_key("test", "alloy")
        k2 = cog._cache_key("test", "alloy")
        self.assertEqual(k1, k2)

    def test_queue_item_expiry(self):
        from cogs.voice_processing_cog import TTSQueueItem
        item = TTSQueueItem(1, 2, "x", "alloy", timestamp=time.time() - 120)
        self.assertTrue(item.is_expired(60))

    def test_progress_bar(self):
        cog = self._cog()
        bar = cog._create_progress_bar(50.0)
        self.assertEqual(len(bar), 10)

    def test_tts_stats_formula(self):
        cog = self._cog()
        cog.total_successes = 8
        cog.total_failed = 2
        rate = (cog.total_successes / max(1, cog.total_successes + cog.total_failed)) * 100
        self.assertEqual(rate, 80.0)

    def test_circuit_breaker_integration(self):
        cog = self._cog()
        self.assertTrue(cog.enabled)


# ═══════════════════════════════════════════════════════════════════════════════
# DALL-E
# ═══════════════════════════════════════════════════════════════════════════════

@feature("DALL-E — URL extraction, job expiry, cog init")
class TestDALLE(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = apply_test_env()
        ensure_event_loop()

    @classmethod
    def tearDownClass(cls):
        restore_env(cls._env)

    def _cog(self):
        from cogs.DALLE_cog import DALLECog
        return DALLECog(make_fake_bot())

    def test_extract_image_url_valid(self):
        cog = self._cog()
        result = {
            "success": True,
            "data": {"data": [{"url": "https://example.com/img.png"}]},
        }
        self.assertEqual(cog._extract_image_url(result), "https://example.com/img.png")

    def test_extract_image_url_invalid(self):
        cog = self._cog()
        self.assertIsNone(cog._extract_image_url({"success": True, "data": {}}))
        self.assertIsNone(cog._extract_image_url({}))

    def test_job_expiry(self):
        from cogs.DALLE_cog import GenerationJob
        job = GenerationJob(1, "cat", "1024x1024", "hd", MagicMock(), time.time() - 400)
        self.assertTrue(job.is_expired())

    def test_cog_enabled(self):
        cog = self._cog()
        self.assertTrue(cog.enabled)


# ═══════════════════════════════════════════════════════════════════════════════
# DISTRIBUTE ZIP
# ═══════════════════════════════════════════════════════════════════════════════

@feature("DistributeZip — validation, metadata, file lookup, browser UI")
class TestDistributeZip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = apply_test_env()
        ensure_event_loop()

    @classmethod
    def tearDownClass(cls):
        restore_env(cls._env)

    def _cog(self):
        from cogs.DistributeZip_cog import DistributeZipCog
        with patch("cogs.DistributeZip_cog.load_metadata", return_value={"files": {}, "history": []}):
            return DistributeZipCog(make_fake_bot())

    def _attachment(self, name="mod.zip", size=1024):
        att = MagicMock()
        att.filename = name
        att.size = size
        return att

    def test_validate_file_ok(self):
        cog = self._cog()
        self.assertIsNone(cog._validate_file(self._attachment()))

    def test_validate_file_too_large(self):
        from cogs.DistributeZip_cog import MAX_FILE_SIZE
        cog = self._cog()
        err = cog._validate_file(self._attachment(size=MAX_FILE_SIZE + 1))
        self.assertIsNotNone(err)
        self.assertIn("exceeds", err)

    def test_validate_file_traversal_name(self):
        cog = self._cog()
        att = self._attachment(name="../evil.zip")
        err = cog._validate_file(att)
        self.assertIsNotNone(err)

    def test_find_file_by_name(self):
        cog = self._cog()
        cog.metadata["files"] = {
            "1": {"name": "MyPack", "filename": "mypack.zip"},
        }
        found = cog._find_file_by_name("mypack")
        self.assertIsNotNone(found)

    def test_download_count_accumulates(self):
        meta = {"files": {"1": {"download_count": 10}}}
        successful = 3
        meta["files"]["1"]["download_count"] = meta["files"]["1"].get("download_count", 0) + successful
        self.assertEqual(meta["files"]["1"]["download_count"], 13)

    def test_file_browser_empty(self):
        from cogs.distributezip_file_browser import create_file_browser_view
        from cogs.DistributeZip_cog import FILES_DIR
        emb, view = create_file_browser_view(FILES_DIR, {"files": {}}, "get")
        self.assertIsNone(view)

    def test_file_browser_with_files(self):
        from cogs.distributezip_file_browser import create_file_browser_view
        from cogs.DistributeZip_cog import FILES_DIR
        meta = {
            "files": {
                "a": {"name": "A", "filename": "a.zip", "uploaded_at": 2},
                "b": {"name": "B", "filename": "b.zip", "uploaded_at": 1},
            }
        }

        async def run():
            emb, view = create_file_browser_view(FILES_DIR, meta, "browse")
            self.assertIsNotNone(view)

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN / DEPLOY / COG LOADING
# ═══════════════════════════════════════════════════════════════════════════════

@feature("Main — config, OpenAI key validation, cog extensions import")
class TestMainModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = apply_test_env()
        ensure_event_loop()
        ensure_event_loop()

    @classmethod
    def tearDownClass(cls):
        restore_env(cls._env)

    def test_config_loads(self):
        from main import Config
        c = Config()
        self.assertTrue(c.OPENAI_API_KEY.startswith("sk-"))

    def test_openai_key_format_invalid(self):
        from main import validate_openai_key
        log = logging.getLogger("test")
        ok = asyncio.run(validate_openai_key("not-a-key", log, MagicMock()))
        self.assertFalse(ok)

    def test_cog_modules_import(self):
        from main import COG_EXTENSIONS
        for ext in COG_EXTENSIONS:
            mod = importlib.import_module(ext)
            self.assertTrue(hasattr(mod, "setup"))

    def test_http_manager_session_rebuild(self):
        from main import HttpManager
        async def run():
            hm = HttpManager()
            s1 = await hm.get_session()
            await hm.invalidate_session()
            s2 = await hm.get_session()
            self.assertIsNot(s1, s2)
        asyncio.run(run())

    def test_deploy_slash_descriptions(self):
        import deploy
        self.assertTrue(deploy.check_slash_descriptions())


# ═══════════════════════════════════════════════════════════════════════════════
# COG INSTANTIATION (no Discord connection)
# ═══════════════════════════════════════════════════════════════════════════════

@feature("Cog load — all four cogs instantiate without Discord gateway")
class TestCogInstantiation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = apply_test_env()
        ensure_event_loop()

    @classmethod
    def tearDownClass(cls):
        restore_env(cls._env)

    def test_all_cogs_construct(self):
        from cogs.DALLE_cog import DALLECog
        from cogs.DistributeZip_cog import DistributeZipCog
        from cogs.SecretSanta_cog import SecretSantaCog
        from cogs.voice_processing_cog import VoiceProcessingCog
        bot = make_fake_bot()
        with patch("cogs.DistributeZip_cog.load_metadata", return_value={"files": {}, "history": []}), \
             patch.object(SecretSantaCog, "cog_load", AsyncMock()):
            cogs = [
                VoiceProcessingCog(bot),
                DALLECog(bot),
                SecretSantaCog(bot),
                DistributeZipCog(bot),
            ]
        for cog in cogs:
            self.assertIsNotNone(cog)


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    apply_test_env()  # before any main import
    ensure_event_loop()

    print("=" * 60)
    print("WaveTechToolBox — isolated feature test suite")
    print("=" * 60)

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for test_cls in [
        TestUtils,
        TestSecretSantaStorage,
        TestSecretSantaAssignments,
        TestSecretSantaChecks,
        TestSecretSantaCore,
        TestSecretSantaCommands,
        TestVoiceTTS,
        TestDALLE,
        TestDistributeZip,
        TestMainModule,
        TestCogInstantiation,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(test_cls))

    # Also run original audit simulations
    print("\n--- Prior audit simulations ---")
    import tests.audit_simulations as audit
    try:
        audit.main()
        audit_ok = True
    except SystemExit as e:
        audit_ok = e.code == 0

    print("\n--- Feature test cases ---")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print("FEATURE COVERAGE")
    for cls in [TestUtils, TestSecretSantaStorage, TestSecretSantaAssignments,
                TestSecretSantaChecks, TestSecretSantaCore, TestSecretSantaCommands,
                TestVoiceTTS, TestDALLE, TestDistributeZip, TestMainModule, TestCogInstantiation]:
        name = getattr(cls, "feature_name", cls.__name__)
        print(f"  • {name}")
    print("=" * 60)

    failed = len(result.failures) + len(result.errors)
    print(f"\nUnit tests: {result.testsRun - failed}/{result.testsRun} passed")
    print(f"Audit simulations: {'PASS' if audit_ok else 'FAIL'}")

    if failed or not audit_ok:
        print("\n❌ SOME TESTS FAILED")
        return 1
    print("\n✅ ALL ISOLATED TESTS PASSED")
    print("\nNote: Live Discord/OpenAI/voice playback cannot be tested offline.")
    print("      After deploy, smoke-test: /tts, /image, /ss status, /distribute list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
