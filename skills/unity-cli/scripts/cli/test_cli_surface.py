"""Lock the intentionally small agent-facing CLI surface."""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


CLI = Path(__file__).resolve().with_name("cs.py")
SPEC = importlib.util.spec_from_file_location("unity_cli_cs_surface", CLI)
CS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CS)


def _help(*args):
    completed = subprocess.run(
        [sys.executable, "-B", str(CLI), *args, "--help"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout


class CliSurfaceTests(unittest.TestCase):
    def test_top_level_commands_are_deliberate(self):
        output = _help()
        for name in (
            "setup",
            "status",
            "exec",
            "command",
            "health",
            "refresh",
            "list-commands",
            "batch",
            "catalog",
            "snippets",
        ):
            self.assertIn(name, output)
        self.assertNotIn("complete", output)

    def test_list_commands_exposes_progressive_filters(self):
        output = _help("list-commands")
        self.assertIn("--view", output)
        self.assertIn("--domain", output)
        self.assertIn("--tier", output)
        self.assertIn("--id", output)
        self.assertIn("--offline", output)
        self.assertIn("--refresh", output)
        self.assertNotIn("--include-blocked", output)
        self.assertNotIn("--type", output)

    def test_list_command_selectors_are_repeatable_and_mutually_exclusive(self):
        mixed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(CLI),
                "list-commands",
                "--offline",
                "--domain",
                "objects",
                "--domain",
                "scene",
                "--id",
                "editor/status",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(2, mixed.returncode)
        self.assertIn("not allowed with argument", mixed.stderr)

    def test_default_discovery_json_is_compact_and_verbose_keeps_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "Project"
            (project / "Assets").mkdir(parents=True)
            (project / "ProjectSettings").mkdir()
            environment = dict(os.environ)
            environment["LOCALAPPDATA"] = str(root / "LocalAppData")
            self._seed_cache(project, environment)
            base = [
                sys.executable,
                "-B",
                str(CLI),
                "list-commands",
                "--project",
                str(project),
                "--offline",
                "--json",
            ]
            compact = subprocess.run(
                base,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                env=environment,
            )
            verbose = subprocess.run(
                [*base, "--verbose"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                env=environment,
            )

        self.assertEqual(0, compact.returncode, compact.stderr)
        self.assertEqual(0, verbose.returncode, verbose.stderr)
        self.assertNotIn("\n  ", compact.stdout)
        self.assertIn("\n  ", verbose.stdout)
        compact_payload = json.loads(compact.stdout)
        verbose_payload = json.loads(verbose.stdout)
        self.assertIn("cacheStored", compact_payload["data"])
        self.assertNotIn("registryGeneration", compact_payload["data"])
        self.assertIn("registryGeneration", verbose_payload["data"])
        self.assertLess(len(compact.stdout), len(verbose.stdout))

    def _seed_cache(self, project, environment):
        """Populate the per-project registry cache as a prior live resolution would."""
        from cli import paths
        from cli.registry_cache import save_registry_cache

        snapshot = json.loads(
            (
                CLI.parent
                / "local_fixtures"
                / "builtin_registry_snapshot.v1.json"
            ).read_text("utf-8")
        )
        snapshot["custom"]["included"] = True
        with mock.patch.dict(
            os.environ,
            {"LOCALAPPDATA": environment["LOCALAPPDATA"]},
        ):
            cache_path = paths.registry_cache_path(Path(project).resolve())
            self.assertTrue(save_registry_cache(cache_path, snapshot))

    def test_first_use_offline_without_cache_fails_with_guidance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "Project"
            (project / "Assets").mkdir(parents=True)
            (project / "ProjectSettings").mkdir()
            environment = dict(os.environ)
            environment["LOCALAPPDATA"] = str(root / "LocalAppData")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(CLI),
                    "list-commands",
                    "--project",
                    str(project),
                    "--offline",
                    "--view",
                    "custom",
                    "--id",
                    "project/example",
                    "--json",
                    "--verbose",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                env=environment,
            )

        self.assertEqual(1, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn(
            "start the Unity editor service once",
            payload["summary"],
        )
        self.assertNotIn("unknown command", payload["summary"])

    def test_old_offline_cache_domain_error_requires_live_discovery(self):
        snapshot = json.loads(
            (
                CLI.parent
                / "local_fixtures"
                / "builtin_registry_snapshot.v1.json"
            ).read_text("utf-8")
        )
        snapshot["builtin"]["commands"] = [
            command
            for command in snapshot["builtin"]["commands"]
            if command["id"] != "editor/status"
        ]
        snapshot["builtin"]["count"] -= 1
        resolution = SimpleNamespace(
            snapshot=snapshot,
            source="cache",
            custom_available=True,
            live_checked=False,
            cache_stored=True,
            stale_reason="",
        )
        args = SimpleNamespace(
            offline=True,
            refresh_registry=False,
            domains=["objects"],
            command_ids=[],
            tier="core",
            view="authoring",
            verbose=False,
        )

        with mock.patch(
            "cli.registry_resolver.RegistryResolver"
        ) as resolver_type:
            resolver_type.return_value.resolve.return_value = resolution
            result = CS._resolve_command_listing(Path("Project"), args)

        self.assertEqual(2, result["exitCode"])
        self.assertIn("incomplete", result["summary"])
        self.assertIn("live discovery", result["summary"])
        self.assertNotIn("unknown command", result["summary"])
        self.assertEqual("cache", result["data"]["source"])
        self.assertEqual(["objects"], result["data"]["requestedDomains"])

    def test_compact_command_output_preserves_canonical_id_and_falsy_results(self):
        for value in (False, 0, [], None):
            with self.subTest(value=value):
                compact = CS._slim_result(
                    {
                        "ok": True,
                        "exitCode": 0,
                        "stage": "command",
                        "id": "public/read",
                        "data": value,
                    }
                )
                self.assertEqual("public/read", compact["id"])
                self.assertIn("data", compact)
                self.assertEqual(value, compact["data"])

    def test_compact_command_output_does_not_treat_business_data_as_health(self):
        business_data = {
            "initialized": True,
            "port": 14501,
            "refreshing": False,
            "editorState": "ready",
        }
        compact = CS._slim_result(
            {
                "ok": True,
                "exitCode": 0,
                "id": "editor/status",
                "data": business_data,
            }
        )
        self.assertEqual(business_data, compact["data"])

    def test_compact_command_output_keeps_business_data_opaque(self):
        collision_shapes = (
            {"resultJson": '{"value":1}'},
            {"command": {"name": "business-value"}},
            {
                "total": 1,
                "succeeded": 1,
                "failed": 0,
                "results": [{"value": "business-value"}],
            },
            {
                "kind": "domain-index",
                "registryGeneration": 7,
                "domains": ["business-value"],
            },
        )
        for business_data in collision_shapes:
            with self.subTest(business_data=business_data):
                compact = CS._slim_result(
                    {
                        "ok": True,
                        "exitCode": 0,
                        "id": "custom/collision",
                        "data": business_data,
                    }
                )
                self.assertEqual(business_data, compact["data"])

    def test_compact_batch_output_keeps_index_id_and_business_data_only(self):
        compact = CS._slim_result(
            {
                "ok": False,
                "exitCode": 3,
                "data": {
                    "total": 2,
                    "succeeded": 1,
                    "failed": 1,
                    "results": [
                        {
                            "index": 0,
                            "id": "public/read",
                            "ok": True,
                            "type": "",
                            "summary": "done",
                            "sessionId": "generated",
                            "data": 0,
                        },
                        {
                            "index": 1,
                            "id": "public/read",
                            "ok": False,
                            "type": "validation_error",
                            "summary": "bad request",
                            "sessionId": "generated",
                            "data": {},
                        },
                    ],
                },
            }
        )
        self.assertEqual(
            [
                {
                    "index": 0,
                    "id": "public/read",
                    "ok": True,
                    "summary": "done",
                    "data": 0,
                },
                {
                    "index": 1,
                    "id": "public/read",
                    "ok": False,
                    "type": "validation_error",
                    "summary": "bad request",
                },
            ],
            compact["data"]["results"],
        )

    def test_structured_commands_require_input_files(self):
        command_help = _help("command")
        self.assertIn("--input", command_help)
        self.assertIn('"id"', command_help)
        self.assertNotIn('"ns"', command_help)
        self.assertIn("--input", _help("batch"))
        self.assertIn("--file", _help("exec"))

    def test_legacy_command_shape_fails_before_project_or_http_lookup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            request_path = Path(temp_dir) / "invalid.json"
            request_path.write_text(
                json.dumps(
                    {
                        "ns": "gameobject",
                        "action": "get",
                        "args": {"path": "Player"},
                    }
                ),
                "utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(CLI),
                    "command",
                    "--input",
                    str(request_path),
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        self.assertEqual(2, completed.returncode)
        self.assertIn("exactly", completed.stderr)
        self.assertIn("id", completed.stderr)
        self.assertNotIn("no Unity project found", completed.stderr)

    def test_batch_rejects_legacy_bare_array_and_non_boolean_stop_policy(self):
        invalid_payloads = (
            [
                {
                    "id": "editor/status",
                    "args": {},
                }
            ],
            {
                "commands": [
                    {
                        "id": "editor/status",
                        "args": {},
                    }
                ],
                "stopOnError": "false",
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp_dir:
                request_path = Path(temp_dir) / "invalid-batch.json"
                request_path.write_text(json.dumps(payload), "utf-8")
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(CLI),
                        "batch",
                        "--input",
                        str(request_path),
                        "--json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
            self.assertEqual(2, completed.returncode)
            self.assertNotIn("no Unity project found", completed.stderr)

    def test_structured_input_rejects_duplicate_keys_and_nonfinite_numbers(self):
        invalid_documents = (
            '{"id":"editor/status","args":{},"args":{}}',
            '{"id":"editor/status","args":{"value":NaN}}',
        )
        for document in invalid_documents:
            with self.subTest(document=document), tempfile.TemporaryDirectory() as temp_dir:
                request_path = Path(temp_dir) / "invalid-command.json"
                request_path.write_text(document, "utf-8")
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(CLI),
                        "command",
                        "--input",
                        str(request_path),
                        "--json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
            self.assertEqual(2, completed.returncode)
            self.assertIn("invalid JSON", completed.stderr)
            self.assertNotIn("no Unity project found", completed.stderr)

    def test_offline_discovery_resolves_local_cache_key_but_never_probes_network(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "Project"
            argv = [
                str(CLI),
                "--mode",
                "runtime",
                "--ip",
                "192.0.2.1",
                "list-commands",
                "--offline",
                "--domain",
                "scene",
                "--tier",
                "core",
                "--json",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    CS,
                    "find_project_root",
                    return_value=project,
                ) as find_project,
                mock.patch.object(
                    CS,
                    "_probe_port",
                    side_effect=AssertionError("offline discovery probed the network"),
                ),
                mock.patch.object(
                    CS,
                    "cmd_list_commands_offline",
                    return_value=0,
                ) as offline_handler,
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as exit_status,
            ):
                CS.main()

        self.assertEqual(0, exit_status.exception.code)
        find_project.assert_called_once()
        offline_handler.assert_called_once()
        self.assertEqual(project, offline_handler.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
