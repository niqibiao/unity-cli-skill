import tempfile
import unittest
from pathlib import Path

from cli.snippets.stats import (
    audit_path, stats_path,
    load_audit, save_audit, init_audit_entry, mark_deprecated,
    load_stats, init_stats_entry,
    record_success, record_failure,
)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_paths_under_unity_cli(self):
        self.assertEqual(audit_path(self.root).name, "snippets-audit.json")
        self.assertEqual(audit_path(self.root).parent.name, ".unity-cli")
        self.assertEqual(stats_path(self.root).name, "snippets-stats.json")

    def test_init_audit_entry(self):
        init_audit_entry(self.root, "scene.x", verified=True, when="2026-05-07T00:00:00Z")
        audit = load_audit(self.root)
        e = audit["snippets"]["scene.x"]
        self.assertEqual(e["created_at"], "2026-05-07T00:00:00Z")
        self.assertEqual(e["verified_at"], "2026-05-07T00:00:00Z")
        self.assertFalse(e["unverified"])
        self.assertFalse(e["deprecated"])

    def test_init_audit_entry_unverified(self):
        init_audit_entry(self.root, "scene.x", verified=False, when="2026-05-07T00:00:00Z")
        e = load_audit(self.root)["snippets"]["scene.x"]
        self.assertTrue(e["unverified"])
        self.assertIsNone(e["verified_at"])

    def test_mark_deprecated(self):
        init_audit_entry(self.root, "scene.x", verified=True, when="2026-05-07T00:00:00Z")
        mark_deprecated(self.root, "scene.x",
                        reason="superseded", supersede="scene.y",
                        when="2026-06-01T00:00:00Z")
        e = load_audit(self.root)["snippets"]["scene.x"]
        self.assertTrue(e["deprecated"])
        self.assertEqual(e["deprecated_reason"], "superseded")
        self.assertEqual(e["supersedes"], "scene.y")
        self.assertEqual(e["deprecated_at"], "2026-06-01T00:00:00Z")


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_stats_entry_uses_created_at_for_last_used(self):
        init_stats_entry(self.root, "scene.x", created_at="2026-05-07T00:00:00Z")
        e = load_stats(self.root)["snippets"]["scene.x"]
        self.assertEqual(e["successes"], 0)
        self.assertEqual(e["failures"], 0)
        self.assertEqual(e["last_used"], "2026-05-07T00:00:00Z")
        self.assertIsNone(e["last_failure"])
        self.assertIsNone(e["first_failure_in_streak"])
        self.assertEqual(e["consecutive_failures"], 0)

    def test_record_success_clears_streak(self):
        init_stats_entry(self.root, "scene.x", created_at="2026-05-07T00:00:00Z")
        record_failure(self.root, "scene.x", when="2026-05-08T00:00:00Z")
        record_failure(self.root, "scene.x", when="2026-05-09T00:00:00Z")
        record_success(self.root, "scene.x", when="2026-05-10T00:00:00Z")
        e = load_stats(self.root)["snippets"]["scene.x"]
        self.assertEqual(e["successes"], 1)
        self.assertEqual(e["failures"], 2)
        self.assertEqual(e["consecutive_failures"], 0)
        self.assertIsNone(e["first_failure_in_streak"])
        self.assertEqual(e["last_used"], "2026-05-10T00:00:00Z")

    def test_record_failure_tracks_streak_window(self):
        init_stats_entry(self.root, "scene.x", created_at="2026-05-07T00:00:00Z")
        record_failure(self.root, "scene.x", when="2026-05-08T00:00:00Z")
        record_failure(self.root, "scene.x", when="2026-05-15T00:00:00Z")
        e = load_stats(self.root)["snippets"]["scene.x"]
        self.assertEqual(e["consecutive_failures"], 2)
        self.assertEqual(e["first_failure_in_streak"], "2026-05-08T00:00:00Z")
        self.assertEqual(e["last_failure"], "2026-05-15T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
