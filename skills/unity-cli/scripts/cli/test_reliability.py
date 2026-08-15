"""Unit tests for the reliability doctor / wait-ready engine (fake adapter + clock)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.reliability import (  # noqa: E402
    PLAY_MODE_DEBOUNCE_POLLS,
    ReliabilityCoordinator,
    _health_is_ready,
)


def make_health(**overrides):
    operation = {
        "opId": "op-current",
        "phase": "ready",
        "compileRequested": True,
        "generation": 3,
        "message": "Assembly reload finished",
    }
    operation.update(overrides.pop("operation", {}))
    base = {
        "ok": True,
        "initialized": True,
        "isEditor": True,
        "port": 14500,
        "refreshing": False,
        "generation": operation["generation"],
        "editorState": "ready",
        "packageVersion": "2.1.0",
        "protocolVersion": 1,
        "unityVersion": "2022.3.10f1",
        "isCompiling": False,
        "compileFailed": False,
        "isUpdating": False,
        "isPlaying": False,
        "mainThreadHeartbeatAgeMs": 10,
        "scriptChangesWhilePlaying": "recompile_after_finished",
        "operation": operation,
    }
    base.update(overrides)
    return base


def compiling_in_play(**overrides):
    """A play-mode hold: active phase, compile requested, editor playing."""
    defaults = {
        "editorState": "compiling",
        "refreshing": True,
        "isCompiling": True,
        "isPlaying": True,
        "operation": {"phase": "compiling", "message": "Script compilation in progress"},
    }
    defaults.update(overrides)
    return make_health(**defaults)


class FakeAdapter:
    """Serves one queued health payload per poll; the last one repeats."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def post(self, port, endpoint, payload, timeout=2.0):
        self.calls += 1
        item = self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0]
        if isinstance(item, Exception):
            raise item
        return json.dumps({"ok": True, "dataJson": json.dumps(item)})


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.001)


class CoordinatorCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project_root = Path(self._tmp.name)
        self.package_dir = self.project_root / "pkg"
        self.package_dir.mkdir()
        self.write_package_version("2.1.0")

    def write_package_version(self, version):
        (self.package_dir / "package.json").write_text(
            json.dumps({"version": version}), "utf-8"
        )

    def coordinator(self, payloads, package_dir="default"):
        clock = FakeClock()
        return ReliabilityCoordinator(
            self.project_root,
            package_dir=self.package_dir if package_dir == "default" else package_dir,
            adapter=FakeAdapter(payloads),
            clock=clock,
            sleeper=clock.sleep,
        )

    @staticmethod
    def codes(report):
        return {item["code"] for item in report["data"]["findings"]}


class HealthReductionTests(CoordinatorCase):
    def test_ready_when_all_conditions_hold(self):
        report = self.coordinator([make_health()]).wait_ready(10)
        self.assertTrue(report["ok"])
        self.assertEqual(report["exitCode"], 0)

    def test_play_mode_alone_is_still_ready(self):
        self.assertTrue(_health_is_ready(make_health(isPlaying=True)))

    def test_stale_heartbeat_blocks_ready(self):
        self.assertFalse(_health_is_ready(make_health(mainThreadHeartbeatAgeMs=6000)))

    def test_unrecorded_heartbeat_blocks_ready(self):
        self.assertFalse(_health_is_ready(make_health(mainThreadHeartbeatAgeMs=-1)))

    def test_missing_heartbeat_field_degrades_to_phase_checks(self):
        payload = make_health()
        del payload["mainThreadHeartbeatAgeMs"]
        report = self.coordinator([payload]).doctor()
        self.assertTrue(report["ok"])
        self.assertEqual(report["exitCode"], 0)
        self.assertIn("health.heartbeat_missing", self.codes(report))

    def test_compile_failed_blocks_ready(self):
        report = self.coordinator([make_health(compileFailed=True)]).doctor()
        self.assertFalse(report["ok"])
        self.assertIn("editor.compile_failed", self.codes(report))


class PlayModeDeferralTests(CoordinatorCase):
    def test_recompile_after_finished_fails_on_first_poll(self):
        coordinator = self.coordinator([compiling_in_play()])
        report = coordinator.wait_ready(60)
        self.assertFalse(report["ok"])
        self.assertEqual(report["exitCode"], 3)
        self.assertEqual(report["type"], "play_mode_deferring_compile")
        self.assertIn("editor.play_mode_deferring_compile", self.codes(report))
        self.assertEqual(coordinator.adapter.calls, 1)

    def test_exit_playmode_granted_keeps_waiting(self):
        coordinator = self.coordinator([
            compiling_in_play(),
            compiling_in_play(),
            make_health(),
        ])
        report = coordinator.wait_ready(60, exit_playmode_granted=True)
        self.assertTrue(report["ok"])

    def test_stop_and_recompile_keeps_waiting(self):
        coordinator = self.coordinator([
            compiling_in_play(scriptChangesWhilePlaying="stop_and_recompile"),
            make_health(),
        ])
        report = coordinator.wait_ready(60)
        self.assertTrue(report["ok"])

    def test_recompile_and_continue_keeps_waiting(self):
        coordinator = self.coordinator([
            compiling_in_play(scriptChangesWhilePlaying="recompile_and_continue"),
            make_health(),
        ])
        report = coordinator.wait_ready(60)
        self.assertTrue(report["ok"])

    def test_missing_preference_debounces_then_reports(self):
        payload = compiling_in_play()
        del payload["scriptChangesWhilePlaying"]
        coordinator = self.coordinator([payload])
        report = coordinator.wait_ready(60)
        self.assertFalse(report["ok"])
        self.assertEqual(report["type"], "play_mode_deferring_compile")
        self.assertEqual(coordinator.adapter.calls, PLAY_MODE_DEBOUNCE_POLLS)

    def test_missing_preference_recovering_resets_debounce(self):
        payload = compiling_in_play()
        del payload["scriptChangesWhilePlaying"]
        coordinator = self.coordinator([payload, payload, make_health()])
        report = coordinator.wait_ready(60)
        self.assertTrue(report["ok"])

    def test_doctor_reports_deferral(self):
        report = self.coordinator([compiling_in_play()]).doctor()
        self.assertFalse(report["ok"])
        self.assertEqual(report["type"], "play_mode_deferring_compile")

    def test_requested_phase_is_not_deferral_evidence(self):
        # compileRequested is still the creation default before the trigger
        # runs; a no-change refresh during play must not fast-fail.
        pending = compiling_in_play(
            editorState="requested",
            isCompiling=False,
            operation={"phase": "requested", "message": "Refresh requested"},
        )
        coordinator = self.coordinator([pending, make_health()])
        report = coordinator.wait_ready(60)
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(coordinator.adapter.calls, 2)


class OperationBindingTests(CoordinatorCase):
    def test_min_generation_ignores_stale_compile_failed(self):
        stale = make_health(
            compileFailed=True,
            generation=2,
            operation={"opId": "op-old", "phase": "ready", "generation": 2},
        )
        fresh = make_health(operation={"opId": "op-new", "generation": 3})
        coordinator = self.coordinator([stale, fresh])
        report = coordinator.wait_ready(
            60, expected_operation_id="op-new", minimum_generation=3
        )
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(coordinator.adapter.calls, 2)

    def test_active_expected_operation_ignores_stale_compile_failed(self):
        holding = make_health(
            compileFailed=True,
            editorState="compiling",
            refreshing=True,
            isCompiling=True,
            operation={"opId": "op-new", "phase": "compiling"},
        )
        done = make_health(operation={"opId": "op-new"})
        report = self.coordinator([holding, done]).wait_ready(
            60, expected_operation_id="op-new"
        )
        self.assertTrue(report["ok"])

    def test_compile_failed_terminates_matching_terminal_operation(self):
        failed = make_health(
            compileFailed=True,
            operation={"opId": "op-new", "phase": "ready"},
        )
        report = self.coordinator([failed]).wait_ready(
            60, expected_operation_id="op-new"
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["exitCode"], 3)
        self.assertIn("editor.compile_failed", self.codes(report))

    def test_refresh_failed_terminates_matching_operation(self):
        failed = make_health(
            editorState="failed",
            operation={"opId": "op-new", "phase": "failed", "message": "boom"},
        )
        report = self.coordinator([failed]).wait_ready(
            60, expected_operation_id="op-new"
        )
        self.assertFalse(report["ok"])
        self.assertIn("editor.refresh_failed", self.codes(report))

    def test_timeout_with_active_operation_is_exit_4(self):
        holding = make_health(
            editorState="compiling",
            refreshing=True,
            isCompiling=True,
            operation={"opId": "op-new", "phase": "compiling"},
        )
        report = self.coordinator([holding]).wait_ready(
            2, expected_operation_id="op-new"
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["exitCode"], 4)
        self.assertEqual(report["type"], "operation_in_progress")
        self.assertTrue(report["data"].get("timedOut"))

    def test_plain_timeout_is_exit_3(self):
        holding = make_health(
            editorState="compiling",
            refreshing=True,
            isCompiling=True,
            operation={"phase": "compiling"},
        )
        report = self.coordinator([holding]).wait_ready(2)
        self.assertFalse(report["ok"])
        self.assertEqual(report["exitCode"], 3)
        self.assertEqual(report["type"], "wait_timeout")


class OfflineCheckTests(CoordinatorCase):
    def test_unreachable_service(self):
        report = self.coordinator([OSError("connection refused")]).doctor()
        self.assertFalse(report["ok"])
        self.assertEqual(report["exitCode"], 3)
        self.assertIn("service.unreachable", self.codes(report))

    def test_version_mismatch_is_terminal(self):
        self.write_package_version("1.0.0")
        coordinator = self.coordinator([make_health()])
        report = coordinator.wait_ready(60)
        self.assertFalse(report["ok"])
        self.assertIn("version.mismatch", self.codes(report))
        self.assertEqual(coordinator.adapter.calls, 1)

    def test_package_missing(self):
        report = self.coordinator([make_health()], package_dir=None).doctor()
        self.assertFalse(report["ok"])
        self.assertIn("package.not_installed", self.codes(report))

    def test_unsupported_unity_line(self):
        report = self.coordinator([make_health(unityVersion="2021.3.1f1")]).doctor()
        self.assertFalse(report["ok"])
        self.assertIn("unity.unsupported", self.codes(report))


if __name__ == "__main__":
    unittest.main()
