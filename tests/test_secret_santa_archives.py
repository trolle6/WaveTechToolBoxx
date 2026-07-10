"""Tests for Secret Santa archive loading and history counts."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from cogs.secret_santa_assignments import load_history_from_archives
from cogs.secret_santa_storage import (
    ARCHIVE_DIR,
    TEST_ARCHIVE_YEAR,
    count_event_participants,
    is_valid_archive_year,
    load_all_archives,
    load_json,
    normalize_archive,
)


class SecretSantaArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.archives = load_all_archives()

    def test_all_real_years_on_disk_are_unified(self) -> None:
        for path in ARCHIVE_DIR.glob("[0-9]*.json"):
            if "backups" in path.parts or "_backup_" in path.name:
                continue
            data = load_json(path)
            self.assertIn("event", data, msg=f"{path.name} must use unified format")
            self.assertIsInstance(data["event"].get("assignments"), dict)

    def test_expected_participant_counts(self) -> None:
        expected = {
            2021: 7,
            2022: 22,
            2023: 20,
            2024: 15,
            2025: 24,
        }
        for year, count in expected.items():
            event = self.archives[year]["event"]
            self.assertEqual(count_event_participants(event), count, year)

    def test_2023_special_gift_preserved(self) -> None:
        special = self.archives[2023]["event"].get("special_gifts") or []
        self.assertEqual(len(special), 1)
        self.assertIn("giver_ids", special[0])

    def test_archive_3000_exists_with_all_historical_users(self) -> None:
        self.assertIn(TEST_ARCHIVE_YEAR, self.archives)
        archive = self.archives[TEST_ARCHIVE_YEAR]
        self.assertTrue(archive.get("test_archive"))
        n = count_event_participants(archive["event"])
        self.assertGreaterEqual(n, 40)
        assignments = archive["event"]["assignments"]
        self.assertEqual(len(assignments), n)

    def test_3000_excluded_from_shuffle_history(self) -> None:
        history, _ = load_history_from_archives(ARCHIVE_DIR)
        real_pair_count = 0
        for year, data in self.archives.items():
            if year == TEST_ARCHIVE_YEAR or data.get("test_archive"):
                continue
            assignments = data["event"].get("assignments") or {}
            real_pair_count += len(assignments)
        loaded_pair_count = sum(len(v) for v in history.values())
        self.assertEqual(loaded_pair_count, real_pair_count)

    def test_valid_archive_year_allows_3000(self) -> None:
        self.assertTrue(is_valid_archive_year(3000, archived_years=self.archives.keys()))
        self.assertFalse(is_valid_archive_year(1999, archived_years=self.archives.keys()))

    def test_normalize_legacy_roundtrip(self) -> None:
        legacy = {
            "year": 2099,
            "assignments": [
                {
                    "giver_id": "1",
                    "giver_name": "A",
                    "receiver_id": "2",
                    "receiver_name": "B",
                    "gift": "test gift",
                }
            ],
        }
        unified = normalize_archive(legacy, 2099)
        self.assertEqual(count_event_participants(unified["event"]), 2)
        self.assertIn("1", unified["event"]["gift_submissions"])


if __name__ == "__main__":
    unittest.main()
