"""CLI-layer tests for cs snippets use / prune.

These cover the failure-taxonomy and prune semantics that live in cs.py
handlers (not in the cli.snippets modules):

- environment errors (type=system_error) must NOT count as failures
- Run-body errors must count, and a qualifying streak auto-deprecates
- default `prune` is a no-op on live snippets (broken or not)
- `prune --cold --remove` must not lose deprecations written mid-run
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli.cs as cs
from cli.snippets.store import write_snippet_file
from cli.snippets.stats import (
    init_audit_entry, init_stats_entry, load_audit, load_stats,
    save_audit, save_stats,
)

SNIPPET_ID = "scene.count_objects"
SNIPPET_MD = """---
id: scene.count_objects
summary: Count objects in a layer
safety: read-only
args:
  - name: layerName
    type: string
example:
  layerName: "Default"
---

```csharp
static int Run(string layerName) {
    return 1;
}
```
"""


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_ago(n):
    return _iso(datetime.now(timezone.utc) - timedelta(days=n))


def _use_args(**over):
    base = dict(snippet_id=SNIPPET_ID, snippet_args='{"layerName": "Default"}',
                dry_run=False, as_json=True)
    base.update(over)
    return SimpleNamespace(**base)


def _prune_args(**over):
    base = dict(cold=False, remove=False, max_age_days=30, dry_run=False,
                as_json=True)
    base.update(over)
    return SimpleNamespace(**base)


class _BaseTmpProject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _register(self, snippet_id=SNIPPET_ID, text=SNIPPET_MD):
        write_snippet_file(self.root, snippet_id, text)
        init_audit_entry(self.root, snippet_id, verified=True)
        init_stats_entry(self.root, snippet_id, created_at=_days_ago(30))

    def _stats_entry(self, snippet_id=SNIPPET_ID):
        return load_stats(self.root)["snippets"][snippet_id]

    def _audit_entry(self, snippet_id=SNIPPET_ID):
        return load_audit(self.root)["snippets"][snippet_id]


class UseStatsTaxonomyTests(_BaseTmpProject):
    def _run_use(self, response):
        session = SimpleNamespace(exec=lambda code: response,
                                  emit=lambda r: None)
        with mock.patch("cli.core_bridge.find_package_dir",
                        return_value=Path(self.root)), \
             mock.patch.object(cs, "_new_session", return_value=session), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return cs.cmd_snippets_use(self.root, _use_args(), None)

    def test_success_records_success(self):
        self._register()
        self._run_use({"ok": True, "exitCode": 0, "data": {"text": "1"}})
        e = self._stats_entry()
        self.assertEqual(e["successes"], 1)
        self.assertEqual(e["failures"], 0)

    def test_system_error_not_counted_as_failure(self):
        """Unity down / network error => ok=false envelope with
        type=system_error (proven against core_bridge). Must not poison
        the failure streak."""
        self._register()
        rc = self._run_use({"ok": False, "exitCode": 3,
                            "type": "system_error", "stage": "execute",
                            "summary": "Error post: connection refused"})
        e = self._stats_entry()
        self.assertEqual(e["failures"], 0)
        self.assertEqual(e["consecutive_failures"], 0)
        self.assertEqual(rc, 3)

    def test_run_body_error_counts_as_failure(self):
        self._register()
        self._run_use({"ok": False, "exitCode": 1,
                       "type": "compile_error", "stage": "execute",
                       "summary": "CS0103: name does not exist"})
        e = self._stats_entry()
        self.assertEqual(e["failures"], 1)
        self.assertEqual(e["consecutive_failures"], 1)

    def test_qualifying_streak_auto_deprecates(self):
        """5th consecutive Run-body failure with the streak spanning >= 7d
        auto-deprecates the snippet (spec: Stats & Aging, broken rule)."""
        self._register()
        stats = load_stats(self.root)
        stats["snippets"][SNIPPET_ID].update({
            "failures": 4, "consecutive_failures": 4,
            "first_failure_in_streak": _days_ago(10),
            "last_failure": _days_ago(1),
            "last_used": _days_ago(1),
        })
        save_stats(self.root, stats)

        self._run_use({"ok": False, "exitCode": 1,
                       "type": "compile_error", "summary": "boom"})

        a = self._audit_entry()
        self.assertTrue(a["deprecated"])
        self.assertIn("5 consecutive failures", a["deprecated_reason"])

    def test_short_streak_does_not_auto_deprecate(self):
        """5 strikes inside a single bad session (< 7d span) must not trip."""
        self._register()
        stats = load_stats(self.root)
        stats["snippets"][SNIPPET_ID].update({
            "failures": 4, "consecutive_failures": 4,
            "first_failure_in_streak": _days_ago(1),
            "last_failure": _days_ago(0),
            "last_used": _days_ago(0),
        })
        save_stats(self.root, stats)

        self._run_use({"ok": False, "exitCode": 1,
                       "type": "compile_error", "summary": "boom"})

        self.assertFalse(self._audit_entry()["deprecated"])


class SearchEmptyLibraryTests(_BaseTmpProject):
    def _run_search(self, query="anything"):
        args = SimpleNamespace(query=query, top=5, as_json=True)
        with redirect_stdout(io.StringIO()) as out:
            rc = cs.cmd_snippets_search(self.root, args)
        return rc, json.loads(out.getvalue())

    def test_empty_library_returns_fast_path_marker(self):
        rc, payload = self._run_search()
        self.assertEqual(rc, 0)
        self.assertTrue(payload["data"]["libraryEmpty"])
        self.assertIn("empty", payload["summary"])

    def test_non_empty_library_has_no_marker(self):
        self._register()
        rc, payload = self._run_search("count objects")
        self.assertEqual(rc, 0)
        self.assertNotIn("libraryEmpty", payload["data"])
        self.assertEqual(payload["data"]["results"][0]["id"], SNIPPET_ID)


class PruneSemanticsTests(_BaseTmpProject):
    def _run_prune(self, args):
        with redirect_stdout(io.StringIO()) as out:
            rc = cs.cmd_snippets_prune(self.root, args)
        return rc, out.getvalue()

    def _make_broken_live(self, snippet_id=SNIPPET_ID):
        self._register(snippet_id, SNIPPET_MD.replace(SNIPPET_ID, snippet_id))
        stats = load_stats(self.root)
        stats["snippets"][snippet_id].update({
            "failures": 6, "consecutive_failures": 6,
            "first_failure_in_streak": _days_ago(20),
            "last_failure": _days_ago(1),
            "last_used": _days_ago(1),
        })
        save_stats(self.root, stats)

    def test_default_prune_is_noop_on_live_snippets(self):
        """Spec: default prune only acts on already-deprecated entries;
        broken auto-deprecation happens at use time, not here."""
        self._make_broken_live()
        rc, out = self._run_prune(_prune_args())
        self.assertEqual(rc, 0)
        self.assertFalse(self._audit_entry()["deprecated"])
        payload = json.loads(out)
        self.assertEqual(payload["data"]["deprecate"], [])
        self.assertEqual(payload["data"]["remove"], [])

    def test_cold_remove_preserves_fresh_deprecations(self):
        """Regression: prune --cold --remove used to write back a stale
        in-memory audit after mark_deprecated, losing the deprecations it
        had just written."""
        cold_id = "scene.cold_one"
        old_id = "scene.old_dead"
        # live cold snippet: never used since creation 100d ago
        self._register(cold_id, SNIPPET_MD.replace(SNIPPET_ID, cold_id))
        stats = load_stats(self.root)
        stats["snippets"][cold_id]["last_used"] = _days_ago(100)
        save_stats(self.root, stats)
        # long-deprecated snippet eligible for removal
        self._register(old_id, SNIPPET_MD.replace(SNIPPET_ID, old_id))
        audit = load_audit(self.root)
        audit["snippets"][old_id].update({
            "deprecated": True, "deprecated_at": _days_ago(40),
            "deprecated_reason": "obsolete",
        })
        save_audit(self.root, audit)

        rc, _ = self._run_prune(_prune_args(cold=True, remove=True))
        self.assertEqual(rc, 0)

        audit = load_audit(self.root)
        self.assertNotIn(old_id, audit["snippets"])         # removed
        self.assertTrue(audit["snippets"][cold_id]["deprecated"])  # kept!

    def test_dry_run_takes_no_action(self):
        self._make_broken_live()
        cold_id = "scene.cold_one"
        self._register(cold_id, SNIPPET_MD.replace(SNIPPET_ID, cold_id))
        stats = load_stats(self.root)
        stats["snippets"][cold_id]["last_used"] = _days_ago(100)
        save_stats(self.root, stats)

        rc, _ = self._run_prune(_prune_args(cold=True, dry_run=True))
        self.assertEqual(rc, 0)
        self.assertFalse(self._audit_entry(cold_id)["deprecated"])


class DoctorTests(_BaseTmpProject):
    def _run_doctor(self, revalidate=False, session=None):
        args = SimpleNamespace(revalidate=revalidate, as_json=True)
        ctx = mock.patch("cli.core_bridge.find_package_dir",
                         return_value=Path(self.root)) if session else None
        with redirect_stdout(io.StringIO()) as out:
            if session:
                with ctx, mock.patch.object(cs, "_new_session",
                                            return_value=session):
                    rc = cs.cmd_snippets_doctor(self.root, args, None)
            else:
                rc = cs.cmd_snippets_doctor(self.root, args, None)
        return rc, json.loads(out.getvalue())

    def _types(self, payload):
        return [f["type"] for f in payload["data"]["findings"]]

    def test_clean_library_reports_zero_findings(self):
        self._register()
        rc, payload = self._run_doctor()
        self.assertEqual(rc, 0)
        self.assertEqual(payload["data"]["findings"], [])

    def test_orphan_file_detected(self):
        write_snippet_file(self.root, SNIPPET_ID, SNIPPET_MD)  # no audit
        _, payload = self._run_doctor()
        self.assertIn("orphan_file", self._types(payload))

    def test_missing_file_detected(self):
        init_audit_entry(self.root, SNIPPET_ID, verified=True)
        _, payload = self._run_doctor()
        self.assertIn("missing_file", self._types(payload))

    def test_corrupt_file_detected(self):
        self._register()
        write_snippet_file(self.root, SNIPPET_ID, "not a snippet at all")
        _, payload = self._run_doctor()
        self.assertIn("corrupt", self._types(payload))

    def test_removable_after_cooldown(self):
        self._register()
        audit = load_audit(self.root)
        audit["snippets"][SNIPPET_ID].update(
            {"deprecated": True, "deprecated_at": _days_ago(40)})
        save_audit(self.root, audit)
        _, payload = self._run_doctor()
        self.assertIn("removable", self._types(payload))

    def test_revalidate_pass_refreshes_verified_at(self):
        self._register()
        audit = load_audit(self.root)
        audit["snippets"][SNIPPET_ID]["verified_at"] = _days_ago(120)
        save_audit(self.root, audit)
        session = SimpleNamespace(
            health=lambda: {"ok": True},
            exec=lambda code: {"ok": True, "exitCode": 0, "data": {"text": "1"}},
        )
        rc, payload = self._run_doctor(revalidate=True, session=session)
        self.assertEqual(rc, 0)
        self.assertEqual(payload["data"]["revalidated"],
                         {"passed": 1, "failed": 0})
        self.assertGreater(self._audit_entry()["verified_at"], _days_ago(1))

    def test_revalidate_failure_flagged_without_touching_stats(self):
        self._register()
        session = SimpleNamespace(
            health=lambda: {"ok": True},
            exec=lambda code: {"ok": False, "exitCode": 1,
                               "type": "compile_error",
                               "summary": "CS0619: obsolete API"},
        )
        _, payload = self._run_doctor(revalidate=True, session=session)
        self.assertIn("revalidation_failed", self._types(payload))
        e = self._stats_entry()
        self.assertEqual(e["failures"], 0)          # diagnostics ≠ usage
        self.assertFalse(self._audit_entry()["deprecated"])

    def test_revalidate_aborts_when_unity_unreachable(self):
        self._register()
        session = SimpleNamespace(
            health=lambda: {"ok": False, "summary": "connection refused"},
            exec=lambda code: self.fail("must not exec when health fails"),
        )
        rc, payload = self._run_doctor(revalidate=True, session=session)
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
