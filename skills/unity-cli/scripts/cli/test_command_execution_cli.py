"""Local-only top-level TDD coverage for canonical-ID execution."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CLI_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

CLI = CLI_DIR / "cs.py"
SPEC = importlib.util.spec_from_file_location("unity_cli_execution", CLI)
CS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CS)

from cli import core_bridge, registry_resolver  # noqa: E402


def _contract(command_id, wire_namespace, wire_action, partition="builtin"):
    return {
        "id": command_id,
        "wire": {
            "commandNamespace": wire_namespace,
            "action": wire_action,
        },
        "summary": "test",
        "partition": partition,
        "requirements": {
            "editor": False,
            "mainThread": False,
            "sessionId": False,
        },
        "arguments": [],
        "result": {
            "kind": "empty",
            "format": "",
            "nullable": False,
            "enumValues": [],
            "fields": [],
        },
        "rules": [],
    }


def _snapshot(*, custom=False):
    contract = _contract(
        "public/read",
        "internal-only",
        "query",
        "custom" if custom else "builtin",
    )
    return {
        "registryGeneration": "test",
        "builtin": {
            "included": True,
            "commands": [] if custom else [contract],
        },
        "custom": {
            "included": custom,
            "commands": [contract] if custom else [],
        },
    }


class _Resolver:
    source = "cache"
    snapshot = _snapshot()
    instances = []

    def __init__(self, root, *, session=None):
        self.root = root
        self.session = session
        self.resolve_calls = 0
        type(self).instances.append(self)

    def resolve(self):
        self.resolve_calls += 1
        return SimpleNamespace(
            snapshot=type(self).snapshot,
            source=type(self).source,
            custom_available=type(self).snapshot["custom"]["included"],
            live_checked=True,
            cache_stored=True,
            stale_reason="offline" if type(self).source == "stale-cache" else "",
        )


class _Session:
    def __init__(self):
        self.command_calls = []
        self.batch_calls = []

    def command(self, prepared):
        self.command_calls.append(prepared)
        return {
            "ok": True,
            "exitCode": 0,
            "summary": "executed",
            "data": {},
        }

    def batch(self, prepared, stop_on_error=False):
        self.batch_calls.append((prepared, stop_on_error))
        return {
            "ok": True,
            "exitCode": 0,
            "summary": "executed batch",
            "data": {
                "total": len(prepared),
                "succeeded": len(prepared),
                "failed": 0,
                "results": [
                    {"id": item["id"], "ok": True}
                    for item in prepared
                ],
            },
        }


class CanonicalCommandExecutionCliTests(unittest.TestCase):
    def setUp(self):
        _Resolver.instances = []
        _Resolver.source = "cache"
        _Resolver.snapshot = _snapshot()

    def _invoke(self, payload, command="command", extra_argv=()):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        temp = stack.enter_context(tempfile.TemporaryDirectory())
        root = Path(temp)
        project = root / "Project"
        (project / "Assets").mkdir(parents=True)
        (project / "ProjectSettings").mkdir()
        package = root / "Package"
        package.mkdir()
        request_path = root / "request.json"
        request_path.write_text(json.dumps(payload), "utf-8")
        session = _Session()
        argv = [
            str(CLI),
            command,
            "--project",
            str(project),
            "--input",
            str(request_path),
            *extra_argv,
            "--json",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        stack.enter_context(mock.patch.object(sys, "argv", argv))
        stack.enter_context(
            mock.patch.object(
                core_bridge,
                "find_package_dir",
                return_value=package,
            )
        )
        stack.enter_context(mock.patch.object(CS, "_new_session", return_value=session))
        stack.enter_context(
            mock.patch.object(
                registry_resolver,
                "RegistryResolver",
                _Resolver,
            )
        )
        stack.enter_context(contextlib.redirect_stdout(stdout))
        stack.enter_context(contextlib.redirect_stderr(stderr))
        with self.assertRaises(SystemExit) as exit_status:
            CS.main()
        return exit_status.exception.code, stdout.getvalue(), stderr.getvalue(), session

    def test_single_resolves_once_and_maps_canonical_id_through_contract_wire(self):
        code, output, error, session = self._invoke(
            {"id": "public/read", "args": {}},
        )

        self.assertEqual(0, code, error)
        self.assertTrue(json.loads(output)["ok"])
        self.assertNotIn("\n  ", output)
        self.assertEqual(1, len(_Resolver.instances))
        self.assertEqual(1, _Resolver.instances[0].resolve_calls)
        self.assertEqual(
            [
                {
                    "id": "public/read",
                    "partition": "builtin",
                    "wire": {
                        "commandNamespace": "internal-only",
                        "action": "query",
                    },
                    "args": {},
                }
            ],
            session.command_calls,
        )
        self.assertEqual([], session.batch_calls)

    def test_preflight_error_allows_registry_control_but_never_target_dispatch(self):
        code, output, error, session = self._invoke(
            {"id": "missing/command", "args": {}},
        )

        self.assertEqual(2, code, error)
        self.assertIn("unknown command id", json.loads(output)["summary"])
        self.assertEqual([], session.command_calls)
        self.assertEqual([], session.batch_calls)
        self.assertEqual(1, _Resolver.instances[0].resolve_calls)

    def test_stale_or_fallback_registry_is_not_used_for_execution(self):
        for source in ("stale-cache", "fallback"):
            with self.subTest(source=source):
                _Resolver.source = source
                code, output, error, session = self._invoke(
                    {"id": "public/read", "args": {}},
                )
                self.assertEqual(1, code, error)
                self.assertIn("current registry", json.loads(output)["summary"])
                self.assertEqual([], session.command_calls)

    def test_batch_resolves_once_preflights_all_and_preserves_stop_policy(self):
        code, output, error, session = self._invoke(
            {
                "commands": [
                    {"id": "public/read", "args": {}},
                    {"id": "public/read", "args": {}},
                ],
                "stopOnError": True,
            },
            command="batch",
        )

        self.assertEqual(0, code, error)
        self.assertTrue(json.loads(output)["ok"])
        self.assertNotIn("\n  ", output)
        self.assertEqual(1, len(_Resolver.instances))
        self.assertEqual(1, _Resolver.instances[0].resolve_calls)
        self.assertEqual(1, len(session.batch_calls))
        prepared, stop_on_error = session.batch_calls[0]
        self.assertEqual(["public/read", "public/read"], [item["id"] for item in prepared])
        self.assertTrue(stop_on_error)
        self.assertEqual([], session.command_calls)

    def test_custom_command_executes_only_when_present_in_current_snapshot(self):
        _Resolver.snapshot = _snapshot(custom=True)
        for source in ("live", "cache"):
            with self.subTest(source=source):
                _Resolver.source = source
                code, output, error, session = self._invoke(
                    {"id": "public/read", "args": {}},
                )
                self.assertEqual(0, code, error)
                self.assertEqual("custom", session.command_calls[0]["partition"])


if __name__ == "__main__":
    unittest.main()
