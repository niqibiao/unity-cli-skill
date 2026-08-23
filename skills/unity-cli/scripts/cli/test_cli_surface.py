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


class PortProbeTests(unittest.TestCase):
    """How the runtime port is located.

    A bare TCP connect left the service cleaning up a connection that never
    sent a request, and the call that followed was reset often enough to
    measure. The probe asks a real question instead, and asks the usual port
    alone before fanning out, because the service dispatches one request at a
    time.
    """

    def test_the_first_port_is_tried_alone(self):
        asked = []

        def urlopen(request, timeout=None):
            asked.append(request.full_url)
            response = mock.MagicMock()
            response.read.return_value = b"{}"
            response.__enter__.return_value = response
            return response

        with mock.patch("urllib.request.urlopen", side_effect=urlopen):
            found = CS._probe_port("127.0.0.1", 15500, 10)

        self.assertEqual(15500, found)
        self.assertEqual(1, len(asked), "the common path must not fan out")
        self.assertIn("15500", asked[0])

    def test_the_rest_of_the_range_is_tried_when_the_first_is_silent(self):
        def urlopen(request, timeout=None):
            if "15503" not in request.full_url:
                raise OSError("refused")
            response = mock.MagicMock()
            response.read.return_value = b"{}"
            response.__enter__.return_value = response
            return response

        with mock.patch("urllib.request.urlopen", side_effect=urlopen):
            found = CS._probe_port("127.0.0.1", 15500, 10)

        self.assertEqual(15503, found)

    def test_a_port_answering_something_else_is_not_the_service(self):
        import urllib.error

        def urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 404, "nope", {}, io.BytesIO(b"{}"))

        with mock.patch("urllib.request.urlopen", side_effect=urlopen):
            self.assertIsNone(CS._probe_port("127.0.0.1", 15500, 10))

    def test_nothing_listening_returns_none(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            self.assertIsNone(CS._probe_port("127.0.0.1", 15500, 10))


class DownloadWindowTests(unittest.TestCase):
    """The range reader that both `cs pull` and `cs logs` are built on.

    A follower has to tell "no new bytes yet" from "the file was rotated and is
    now shorter than where I was reading", and the service answers both with
    416 -- the size it reports is the only thing that separates them.
    """

    def _http_error(self, code, body):
        import urllib.error

        return urllib.error.HTTPError(
            "http://host/download", code, "range", {}, io.BytesIO(body)
        )

    def _envelope(self, file_size):
        return json.dumps(
            {"ok": False, "summary": "range", "dataJson": json.dumps({"fileSize": file_size})}
        ).encode("utf-8")

    def test_range_response_reports_chunk_and_total(self):
        response = mock.MagicMock()
        response.read.return_value = b"abc"
        response.headers = {"Content-Range": "bytes 10-12/99"}
        response.__enter__.return_value = response

        with mock.patch("urllib.request.urlopen", return_value=response):
            chunk, total = CS._download_window("http://host/download", 10, 3, 5)

        self.assertEqual(b"abc", chunk)
        self.assertEqual(99, total)

    def test_whole_file_response_has_no_total(self):
        # The service answers 200 without Content-Range when the range covers
        # the entire file, so a caller must not assume a total is always there.
        response = mock.MagicMock()
        response.read.return_value = b"abc"
        response.headers = {}
        response.__enter__.return_value = response

        with mock.patch("urllib.request.urlopen", return_value=response):
            chunk, total = CS._download_window("http://host/download", 0, 99, 5)

        self.assertEqual(b"abc", chunk)
        self.assertIsNone(total)

    def test_416_reports_the_size_the_service_saw(self):
        error = self._http_error(416, self._envelope(42))

        with mock.patch("urllib.request.urlopen", side_effect=error):
            chunk, total = CS._download_window("http://host/download", 100, 10, 5)

        self.assertIsNone(chunk)
        self.assertEqual(42, total)

    def test_416_with_an_unreadable_body_degrades_to_unknown_size(self):
        error = self._http_error(416, b"not json")

        with mock.patch("urllib.request.urlopen", side_effect=error):
            chunk, total = CS._download_window("http://host/download", 100, 10, 5)

        self.assertIsNone(chunk)
        self.assertIsNone(total)

    def test_other_http_errors_propagate(self):
        error = self._http_error(404, b"{}")

        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(Exception):
                CS._download_window("http://host/download", 0, 10, 5)


class _StdoutWithBuffer:
    def __init__(self):
        self.buffer = io.BytesIO()


class LogsFollowTests(unittest.TestCase):
    def _args(self, **overrides):
        base = {
            "path": None,
            "since_start": False,
            "interval": 0.001,
            "wait": 1,
            "ip": "127.0.0.1",
            "port": 15500,
            "timeout": 5,
            "background": False,
            "stop": False,
            "state_file": None,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    @staticmethod
    def _sequence(windows):
        """Replay canned windows, then hold on the last one.

        The follow loop polls until its deadline, so a fixed-length sequence
        would run dry long before the run ends."""
        if callable(windows):
            return windows
        remaining = list(windows)

        def take(*_args):
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]

        return take

    @contextlib.contextmanager
    def _target(self, info, windows):
        """Run cmd_logs against a canned runtime/info and range sequence."""
        session = mock.Mock()
        session.request_wire_command.return_value = {
            "ok": True,
            "data": {"resultJson": info},
        }
        sink = _StdoutWithBuffer()
        with (
            mock.patch("cli.core_bridge.find_package_dir", return_value=Path("pkg")),
            mock.patch.object(CS, "_new_session", return_value=session),
            mock.patch.object(CS, "_download_window", side_effect=self._sequence(windows)),
            mock.patch("sys.stdout", sink),
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            yield sink, err

    def test_a_target_with_no_log_file_of_its_own_says_so(self):
        # Android and iOS report an empty consoleLogPath because they write no
        # log file. Defaulting to it would follow nothing forever.
        info = {"platform": "Android", "persistentDataPath": "/data/files", "consoleLogPath": ""}

        with self._target(info, []) as (_, err):
            code = CS.cmd_logs(Path("proj"), self._args())

        self.assertEqual(3, code)
        self.assertIn("Android", err.getvalue())
        self.assertIn("path your project logs to", err.getvalue())

    def test_rotation_restarts_from_the_beginning_of_the_new_file(self):
        info = {
            "platform": "WindowsPlayer",
            "persistentDataPath": "C:/data",
            "consoleLogPath": "C:/data/Player.log",
        }
        windows = [
            (None, 0),           # initial size probe: start at the end
            (b"first\n", 6),     # new bytes appear
            (None, 2),           # file is now shorter than our offset: rotated
            (b"R\n", 2),         # re-read from zero
            (None, 2),           # nothing further
        ]

        with self._target(info, windows) as (sink, err):
            code = CS.cmd_logs(Path("proj"), self._args(wait=1))

        self.assertEqual(0, code)
        self.assertEqual(b"first\nR\n", sink.buffer.getvalue())
        self.assertIn("restarted", err.getvalue())

    def test_a_dropped_connection_is_not_a_disconnect(self):
        # The service drops a connection occasionally under concurrent load --
        # measured at roughly 1 in 18 calls. Treating one as "the player is
        # gone" would end an unbounded follow at the first hiccup.
        info = {
            "platform": "WindowsPlayer",
            "persistentDataPath": "C:/data",
            "consoleLogPath": "C:/data/Player.log",
        }
        outcomes = [
            (None, 0),                       # size probe
            OSError("connection reset"),     # blip
            (b"still here\n", 11),           # recovered
        ]

        def replay(*_args):
            item = outcomes.pop(0) if outcomes else (None, 11)
            if isinstance(item, Exception):
                raise item
            return item

        with self._target(info, replay) as (sink, err):
            code = CS.cmd_logs(Path("proj"), self._args(wait=1))

        self.assertEqual(0, code)
        self.assertEqual(b"still here\n", sink.buffer.getvalue())
        self.assertNotIn("gone", err.getvalue())

    def test_staying_unreachable_ends_an_unbounded_follow(self):
        info = {
            "platform": "WindowsPlayer",
            "persistentDataPath": "C:/data",
            "consoleLogPath": "C:/data/Player.log",
        }
        calls = {"n": 0}

        def replay(*_args):
            # The probe succeeds -- the target was there when the follow began.
            # Every read after it fails, as it would once the player exits.
            calls["n"] += 1
            if calls["n"] == 1:
                return (None, 0)
            raise OSError("connection refused")

        with self._target(info, replay) as (sink, err):
            with mock.patch.object(CS, "LOGS_DISCONNECT_GRACE", 0.05):
                code = CS.cmd_logs(Path("proj"), self._args(wait=0))

        self.assertEqual(0, code)
        self.assertIn("the target is gone", err.getvalue())
        self.assertEqual(b"", sink.buffer.getvalue())

    def test_since_start_reads_from_zero(self):
        info = {
            "platform": "WindowsPlayer",
            "persistentDataPath": "C:/data",
            "consoleLogPath": "C:/data/Player.log",
        }
        calls = []

        def record(url, offset, length, timeout):
            calls.append(offset)
            return (b"body\n", 5) if len(calls) == 2 else (None, 5)

        with self._target(info, record) as (sink, _):
            CS.cmd_logs(Path("proj"), self._args(since_start=True, wait=1))

        # The probe reads byte 0; the first real read must also start at 0
        # rather than skipping to the current end.
        self.assertEqual(0, calls[1])
        self.assertEqual(b"body\n", sink.buffer.getvalue())


class PullPathResolutionTests(unittest.TestCase):
    """Where `cs pull` decides a requested path lives on the target.

    A target on another machine is addressed with a relative path -- on Android
    and iOS the user has no absolute path to give. So an absolute path names a
    file on the target itself and is taken literally; inferring one machine's
    layout from another's would mis-resolve any path that merely contains the
    product name, the player's own install directory among them.
    """

    INFO = {
        "persistentDataPath": "C:/Users/me/AppData/LocalLow/Studio/Game",
        "productName": "Game",
        "companyName": "Studio",
    }

    def test_relative_path_resolves_against_persistent_data_path(self):
        path, how = CS._resolve_remote_path("logs/game.log", self.INFO)

        self.assertEqual(
            "C:/Users/me/AppData/LocalLow/Studio/Game/logs/game.log", path
        )
        self.assertIn("persistentDataPath", how)

    def test_absolute_path_under_persistent_data_path_is_kept(self):
        requested = "C:/Users/me/AppData/LocalLow/Studio/Game/logs/game.log"

        path, how = CS._resolve_remote_path(requested, self.INFO)

        self.assertEqual(requested, path)
        self.assertIn("already under", how)

    def test_absolute_path_containing_the_product_name_is_not_re_anchored(self):
        # The player's own install directory sits under a folder named for the
        # product. Re-anchoring it onto persistentDataPath produced a 404 for a
        # file that exists exactly where it was asked for.
        requested = "E:/Projects/Game/Build/Dev/Game_Data/boot.config"

        path, how = CS._resolve_remote_path(requested, self.INFO)

        self.assertEqual(requested, path)
        self.assertIn("used as given", how)

    def test_backslash_paths_are_normalized(self):
        path, _ = CS._resolve_remote_path(r"logs\game.log", self.INFO)

        self.assertEqual(
            "C:/Users/me/AppData/LocalLow/Studio/Game/logs/game.log", path
        )

    def test_relative_path_survives_a_target_without_persistent_data_path(self):
        path, how = CS._resolve_remote_path("logs/game.log", {})

        self.assertEqual("logs/game.log", path)
        self.assertIn("no persistentDataPath", how)

    def test_absoluteness_is_judged_for_the_target_not_the_local_platform(self):
        # Driving a Windows player from a POSIX host, or an Android device from
        # Windows, is the case this feature exists for. PurePath would answer
        # for whichever platform runs the CLI and mangle the other one.
        for path in (
            "C:/Users/me/AppData/LocalLow/Studio/Game/logs/game.log",
            r"D:\Games\Build\Game_Data\boot.config".replace("\\", "/"),
            "/storage/emulated/0/Android/data/com.studio.game/files/run.log",
            "/var/mobile/Containers/Data/Application/ABC/Documents/run.log",
            "//buildserver/share/Game/logs/game.log",
        ):
            with self.subTest(path=path):
                self.assertTrue(CS._is_absolute_on_target(path))

        for path in ("logs/game.log", "run.log", "a/b/c.txt", "C:relative.txt"):
            with self.subTest(path=path):
                self.assertFalse(CS._is_absolute_on_target(path))

    def test_android_target_takes_a_posix_absolute_path_as_given(self):
        android = {
            "persistentDataPath": "/storage/emulated/0/Android/data/com.studio.game/files",
            "productName": "Game",
            "companyName": "Studio",
        }
        requested = "/storage/emulated/0/Android/data/com.studio.game/files/logs/run.log"

        path, how = CS._resolve_remote_path(requested, android)

        self.assertEqual(requested, path)
        self.assertIn("already under", how)

    def test_windows_path_is_not_glued_onto_a_posix_persistent_data_path(self):
        android = {
            "persistentDataPath": "/storage/emulated/0/Android/data/com.studio.game/files",
            "productName": "Game",
            "companyName": "Studio",
        }

        path, how = CS._resolve_remote_path("C:/Users/me/notes.txt", android)

        self.assertEqual("C:/Users/me/notes.txt", path)
        self.assertIn("used as given", how)


if __name__ == "__main__":
    unittest.main()
