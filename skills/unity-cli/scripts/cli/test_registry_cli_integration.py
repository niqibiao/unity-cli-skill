"""Local-only top-level CLI acceptance seam for Ticket 06."""

import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CLI_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

CLI = CLI_DIR / "cs.py"
SPEC = importlib.util.spec_from_file_location("unity_cli_registry_acceptance", CLI)
CS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CS)

from cli import core_bridge, paths  # noqa: E402


def _envelope(payload):
    return {
        "ok": True,
        "exitCode": 0,
        "data": {"resultJson": payload},
    }


class _Gateway:
    def __init__(self, snapshot, allow_snapshot):
        self.snapshot = snapshot
        self.allow_snapshot = allow_snapshot
        self.calls = []

    def registry_snapshot(self, if_generation=None):
        self.calls.append(("snapshot", if_generation))
        if if_generation == self.snapshot["registryGeneration"]:
            return _envelope({
                "schemaVersion": self.snapshot["schemaVersion"],
                "registryGeneration": self.snapshot["registryGeneration"],
                "unchanged": True,
            })
        if not self.allow_snapshot:
            raise AssertionError("token-equal cache hit transferred a snapshot")
        return _envelope(self.snapshot)


class _FailingGateway:
    def __init__(self):
        self.calls = []

    def registry_snapshot(self, if_generation=None):
        self.calls.append(("snapshot", if_generation))
        raise OSError("request timed out")


class RegistryCliAcceptanceTests(unittest.TestCase):
    def test_top_level_live_then_cache_hit_preserves_json_contract_and_call_sequence(self):
        fallback = json.loads(
            (CLI_DIR / "local_fixtures" / "builtin_registry_snapshot.v1.json").read_text(
                "utf-8"
            )
        )
        live = copy.deepcopy(fallback)
        live["custom"]["included"] = True

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "Project"
            (project / "Assets").mkdir(parents=True)
            (project / "ProjectSettings").mkdir()
            package = root / "Package"
            package.mkdir()
            user_cache = root / "UserCache"

            first_gateway = _Gateway(live, allow_snapshot=True)
            first = self._invoke(
                project,
                package,
                user_cache,
                first_gateway,
                [
                    "--domain",
                    "objects",
                    "--domain",
                    "scene",
                    "--tier",
                    "core",
                ],
            )
            self.assertEqual(
                [("snapshot", None)],
                first_gateway.calls,
            )
            self.assertEqual("live", first["data"]["source"])
            self.assertTrue(first["data"]["liveChecked"])
            self.assertTrue(first["data"]["customAvailable"])
            self.assertTrue(first["data"]["cacheStored"])
            self.assertEqual(
                live["registryGeneration"],
                first["data"]["registryGeneration"],
            )
            self.assertEqual("route-cards", first["data"]["kind"])
            self.assertEqual(
                ["objects", "scene"],
                first["data"]["domains"],
            )
            self.assertTrue(first["data"]["routes"])
            for route in first["data"]["routes"]:
                self.assertNotIn("arguments", route)
                self.assertNotIn("result", route)

            hit_gateway = _Gateway(live, allow_snapshot=False)
            second = self._invoke(
                project,
                package,
                user_cache,
                hit_gateway,
                [
                    "--id",
                    "editor/status",
                    "--id",
                    "gameobject/get",
                    "--id",
                    "editor/menu.open",
                ],
            )
            self.assertEqual(
                [("snapshot", live["registryGeneration"])],
                hit_gateway.calls,
            )
            self.assertEqual("cache", second["data"]["source"])
            self.assertTrue(second["data"]["liveChecked"])
            self.assertEqual("contract-bundle", second["data"]["kind"])
            self.assertIn("1 denied intent", second["summary"])
            self.assertEqual(
                ["editor/status", "gameobject/get"],
                [
                    item["contract"]["id"]
                    for item in second["data"]["selected"]
                ],
            )
            self.assertEqual(
                ["editor/menu.open"],
                [item["id"] for item in second["data"]["denied"]],
            )
            self.assertFalse(second["data"]["denied"][0]["invoke"])

    def test_stale_cache_missing_exact_id_is_unverified_not_unknown(self):
        fallback = json.loads(
            (CLI_DIR / "local_fixtures" / "builtin_registry_snapshot.v1.json").read_text(
                "utf-8"
            )
        )
        live = copy.deepcopy(fallback)
        live["custom"]["included"] = True

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "Project"
            (project / "Assets").mkdir(parents=True)
            (project / "ProjectSettings").mkdir()
            package = root / "Package"
            package.mkdir()
            user_cache = root / "UserCache"

            self._invoke(
                project,
                package,
                user_cache,
                _Gateway(live, allow_snapshot=True),
                ["--view", "custom"],
            )
            failing_gateway = _FailingGateway()
            result = self._invoke(
                project,
                package,
                user_cache,
                failing_gateway,
                ["--view", "custom", "--id", "project/new"],
                expected_exit=2,
            )

        self.assertEqual(
            [("snapshot", live["registryGeneration"])],
            failing_gateway.calls,
        )
        self.assertFalse(result["ok"])
        self.assertIn("unavailable", result["summary"])
        self.assertNotIn("unknown command", result["summary"])
        self.assertEqual("stale-cache", result["data"]["source"])
        self.assertTrue(result["data"]["customAvailable"])
        self.assertEqual("request timed out", result["data"]["staleReason"])
        self.assertEqual(["project/new"], result["data"]["requestedIds"])

    def test_live_missing_exact_id_remains_authoritative_unknown(self):
        live = json.loads(
            (CLI_DIR / "local_fixtures" / "builtin_registry_snapshot.v1.json").read_text(
                "utf-8"
            )
        )
        live["custom"]["included"] = True

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "Project"
            (project / "Assets").mkdir(parents=True)
            (project / "ProjectSettings").mkdir()
            package = root / "Package"
            package.mkdir()
            result = self._invoke(
                project,
                package,
                root / "UserCache",
                _Gateway(live, allow_snapshot=True),
                ["--view", "custom", "--id", "project/missing"],
                expected_exit=2,
            )

        self.assertFalse(result["ok"])
        self.assertIn("unknown command", result["summary"])
        self.assertNotIn("unavailable", result["summary"])
        self.assertEqual("live", result["data"]["source"])
        self.assertTrue(result["data"]["customAvailable"])
        self.assertEqual(["project/missing"], result["data"]["requestedIds"])

    def _invoke(
        self,
        project,
        package,
        user_cache,
        gateway,
        selectors,
        *,
        expected_exit=0,
    ):
        argv = [
            str(CLI),
            "list-commands",
            "--project",
            str(project),
            *selectors,
            "--json",
            "--verbose",
        ]
        stdout = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(paths, "cache_root", return_value=user_cache),
            mock.patch.object(
                core_bridge,
                "find_package_dir",
                return_value=package,
            ),
            mock.patch.object(CS, "_new_session", return_value=gateway),
            contextlib.redirect_stdout(stdout),
            self.assertRaises(SystemExit) as exit_status,
        ):
            CS.main()

        self.assertEqual(expected_exit, exit_status.exception.code)
        return json.loads(stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
