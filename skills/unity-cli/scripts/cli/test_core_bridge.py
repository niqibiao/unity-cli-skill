"""Safety tests for the HTTP retry bridge."""

import contextlib
import errno
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli import core_bridge


def _reject_constant(value):
    raise AssertionError(f"bare JSON constant {value!r} leaked into output")


class _State:
    def current_server_base_url(self):
        return "http://127.0.0.1:14501"


class _Transport:
    class TransportError(Exception):
        pass

    def __init__(self, first_error):
        self.first_error = first_error
        self.calls = 0

    def post_json(self, url_base, endpoint, payload, timeout):
        self.calls += 1
        if self.calls == 1:
            raise self.first_error
        return "ok"


def _wrapped_transport_error(cause):
    error = _Transport.TransportError(str(cause))
    error.__cause__ = cause
    return error


class PostRetrySafetyTests(unittest.TestCase):
    def _post(self, error):
        transport = _Transport(error)
        post = core_bridge._make_post_with_retry(transport, _State(), 10)
        return transport, post

    def test_retries_connection_refused_once(self):
        cause = ConnectionRefusedError(errno.ECONNREFUSED, "refused")
        transport, post = self._post(_wrapped_transport_error(cause))

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            self.assertEqual("ok", post("command", {"action": "create"}))

        self.assertEqual(2, transport.calls)
        sleep.assert_called_once_with(core_bridge._RETRY_DELAY_S)

    def test_retries_winsock_connection_refused_once(self):
        cause = OSError("refused")
        cause.winerror = 10061
        transport, post = self._post(_wrapped_transport_error(cause))

        with mock.patch.object(core_bridge.time, "sleep"):
            self.assertEqual("ok", post("health", {}))

        self.assertEqual(2, transport.calls)

    def test_does_not_retry_timeout(self):
        transport, post = self._post(
            _wrapped_transport_error(TimeoutError("response timed out"))
        )

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            with self.assertRaises(_Transport.TransportError):
                post("command", {"action": "create"})

        self.assertEqual(1, transport.calls)
        sleep.assert_not_called()

    def test_does_not_retry_http_error(self):
        transport, post = self._post(
            _wrapped_transport_error(OSError("HTTP 500 Internal Server Error"))
        )

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            with self.assertRaises(_Transport.TransportError):
                post("command", {"action": "create"})

        self.assertEqual(1, transport.calls)
        sleep.assert_not_called()

    def test_does_not_retry_connection_reset(self):
        transport, post = self._post(
            _wrapped_transport_error(ConnectionResetError("reset after send"))
        )

        with mock.patch.object(core_bridge.time, "sleep") as sleep:
            with self.assertRaises(_Transport.TransportError):
                post("command", {"action": "create"})

        self.assertEqual(1, transport.calls)
        sleep.assert_not_called()


class CommandTimeoutTests(unittest.TestCase):
    def test_strict_command_parser_rejects_malformed_raw_success(self):
        delegate = mock.Mock(return_value={"ok": True})
        command_data = {
            "command": {
                "commandNamespace": "internal-only",
                "action": "query",
                "sessionId": "test-session",
            },
            "resultJson": "{}",
        }
        malformed_envelopes = (
            {
                "ok": "false",
                "stage": "command",
                "type": "system_error",
                "summary": "not actually boolean",
                "sessionId": "test-session",
                "dataJson": json.dumps(command_data),
            },
            {
                "ok": True,
                "stage": "command",
                "type": "ok",
                "summary": "invalid result JSON",
                "sessionId": "test-session",
                "dataJson": json.dumps(
                    {
                        **command_data,
                        "resultJson": '{"value": ',
                    }
                ),
            },
            {
                "ok": True,
                "stage": "command",
                "type": "system_error",
                "summary": "success with error type",
                "sessionId": "test-session",
                "dataJson": json.dumps(command_data),
            },
            {
                "ok": False,
                "stage": "command",
                "type": "ok",
                "summary": "failure with success type",
                "sessionId": "test-session",
                "dataJson": json.dumps(
                    {
                        **command_data,
                        "resultJson": "",
                    }
                ),
            },
        )
        for envelope in malformed_envelopes:
            with self.subTest(envelope=envelope), self.assertRaises(ValueError):
                core_bridge._parse_command_http_response_strict(
                    delegate,
                    json.dumps(envelope),
                    session_id="test-session",
                    mode="editor",
                    run_id="run",
                    duration_ms=1,
                    expected_namespace="internal-only",
                    expected_action="query",
                    expected_session_id="test-session",
                )
        delegate.assert_not_called()

    def test_non_finite_numbers_survive_as_readable_markers(self):
        # A NaN Transform is a state worth inspecting, so the result must stay
        # readable instead of collapsing into a protocol error.
        parsed = core_bridge._load_json_strict(
            '{"x": NaN, "y": Infinity, "z": -Infinity, "w": 1e999, "ok": 1.5}',
            "command resultJson",
        )
        self.assertEqual(
            parsed,
            {
                "x": "NaN",
                "y": "Infinity",
                "z": "-Infinity",
                "w": "1e999",
                "ok": 1.5,
            },
        )
        # Markers must re-serialize as quoted strings, keeping the CLI's own
        # output parseable by a strict JSON reader.
        encoded = json.dumps(parsed)
        self.assertIn('"NaN"', encoded)
        json.loads(encoded, parse_constant=_reject_constant)

    def test_strict_command_parser_keeps_unrouted_failure_diagnostics(self):
        # The package answers with an empty invocation when it cannot parse the
        # request into a command; that summary is the only diagnostic there is.
        delegate = mock.Mock(return_value={"ok": False})
        envelope = {
            "ok": False,
            "stage": "command",
            "type": "system_error",
            "summary": "Failed to process command: NullReferenceException",
            "sessionId": "",
            "dataJson": json.dumps(
                {
                    "command": {
                        "commandNamespace": "",
                        "action": "",
                        "sessionId": "",
                    },
                    "resultJson": "",
                }
            ),
        }

        core_bridge._parse_command_http_response_strict(
            delegate,
            json.dumps(envelope),
            session_id="test-session",
            mode="editor",
            run_id="run",
            duration_ms=1,
            expected_namespace="internal-only",
            expected_action="query",
            expected_session_id="test-session",
        )
        delegate.assert_called_once()

    def test_strict_command_parser_rejects_unrouted_success(self):
        # An empty route is only legitimate on a failure.
        delegate = mock.Mock(return_value={"ok": True})
        envelope = {
            "ok": True,
            "stage": "command",
            "type": "ok",
            "summary": "routeless success",
            "sessionId": "test-session",
            "dataJson": json.dumps(
                {
                    "command": {
                        "commandNamespace": "",
                        "action": "",
                        "sessionId": "test-session",
                    },
                    "resultJson": "{}",
                }
            ),
        }

        with self.assertRaisesRegex(ValueError, "route"):
            core_bridge._parse_command_http_response_strict(
                delegate,
                json.dumps(envelope),
                session_id="test-session",
                mode="editor",
                run_id="run",
                duration_ms=1,
                expected_namespace="internal-only",
                expected_action="query",
                expected_session_id="test-session",
            )
        delegate.assert_not_called()

    def test_strict_command_parser_rejects_mismatched_echo(self):
        delegate = mock.Mock(return_value={"ok": True})
        envelope = {
            "ok": True,
            "stage": "command",
            "type": "ok",
            "summary": "wrong response",
            "sessionId": "other-session",
            "dataJson": json.dumps(
                {
                    "command": {
                        "commandNamespace": "other",
                        "action": "route",
                        "sessionId": "other-session",
                    },
                    "resultJson": "{}",
                }
            ),
        }

        with self.assertRaisesRegex(ValueError, "route|session"):
            core_bridge._parse_command_http_response_strict(
                delegate,
                json.dumps(envelope),
                session_id="test-session",
                mode="editor",
                run_id="run",
                duration_ms=1,
                expected_namespace="internal-only",
                expected_action="query",
                expected_session_id="test-session",
            )
        delegate.assert_not_called()

    def test_strict_command_parser_allows_empty_failed_result(self):
        delegate = mock.Mock(return_value={"ok": False, "exitCode": 1})
        envelope = {
            "ok": False,
            "stage": "command",
            "type": "validation_error",
            "summary": "not found",
            "sessionId": "test-session",
            "dataJson": json.dumps(
                {
                    "command": {
                        "commandNamespace": "internal-only",
                        "action": "query",
                        "sessionId": "test-session",
                    },
                    "resultJson": "",
                }
            ),
        }

        result = core_bridge._parse_command_http_response_strict(
            delegate,
            json.dumps(envelope),
            session_id="test-session",
            mode="editor",
            run_id="run",
            duration_ms=1,
            expected_namespace="internal-only",
            expected_action="query",
            expected_session_id="test-session",
        )

        self.assertFalse(result["ok"])
        delegate.assert_called_once()

    def test_console_session_maps_prepared_canonical_command_and_hides_wire_echo(self):
        class _CommandProtocol:
            def __init__(self):
                self.timeout_seconds = None
                self.call_args = None

            def request_command(self, *args, timeout_seconds):
                self.timeout_seconds = timeout_seconds
                self.call_args = args
                return {
                    "ok": True,
                    "type": "ok",
                    "exitCode": 0,
                    "data": {
                        "command": {
                            "commandNamespace": "internal-only",
                            "action": "query",
                            "sessionId": "test-session",
                        },
                        "resultJson": {"value": 1},
                    },
                }

        session = object.__new__(core_bridge.ConsoleSession)
        session._cmd = _CommandProtocol()
        session._post = object()
        session._parser = type(
            "_Parser", (), {"parse_command_http_response": object()}
        )()
        session._mode_name = lambda: "editor"
        session._session_id = "test-session"
        session._timeout = 37

        result = session.command(
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {"name": "Wall"},
            }
        )

        self.assertEqual("public/read", result["id"])
        self.assertEqual({"value": 1}, result["data"])
        self.assertNotIn("command", result)
        self.assertEqual(37, session._cmd.timeout_seconds)
        self.assertEqual(
            ("internal-only", "query", "test-session", {"name": "Wall"}),
            session._cmd.call_args[3:],
        )

    def test_text_output_supports_scalar_canonical_command_results(self):
        session = object.__new__(core_bridge.ConsoleSession)
        for value, expected in ((False, "false"), (0, "0"), (None, "null")):
            with self.subTest(value=value):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    session._print_text(
                        {
                            "ok": True,
                            "summary": "done",
                            "data": value,
                        }
                    )
                self.assertEqual(expected, output.getvalue().strip())

    def test_single_command_preserves_transport_failure_diagnostics(self):
        result = core_bridge._normalize_command_result(
            {
                "ok": False,
                "type": "system_error",
                "exitCode": 3,
                "summary": "Command request failed: connection refused",
                "data": {},
            },
            "public/read",
            "internal-only",
            "query",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(3, result["exitCode"])
        self.assertEqual("public/read", result["id"])
        self.assertIn("connection refused", result["summary"])

    def test_single_command_preserves_already_decoded_string_scalar(self):
        result = core_bridge._normalize_command_result(
            {
                "ok": True,
                "type": "ok",
                "exitCode": 0,
                "summary": "done",
                "data": {
                    "command": {
                        "commandNamespace": "internal-only",
                        "action": "query",
                    },
                    "resultJson": "false",
                },
            },
            "public/read",
            "internal-only",
            "query",
        )
        self.assertEqual("false", result["data"])

    def test_single_command_rejects_inconsistent_failure_status(self):
        result = core_bridge._normalize_command_result(
            {
                "ok": False,
                "type": "system_error",
                "exitCode": 0,
                "summary": "failure reported with success exit code",
                "data": {},
            },
            "public/read",
            "internal-only",
            "query",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(3, result["exitCode"])
        self.assertIn("status", result["summary"])


class RegistryGatewayTests(unittest.TestCase):
    def test_registry_gateway_uses_strict_raw_command_parser(self):
        class _CommandProtocol:
            def request_command(
                self,
                _post,
                parser,
                current_mode_name,
                _namespace,
                _action,
                session_id,
                _args=None,
                *,
                timeout_seconds,
            ):
                del timeout_seconds
                raw = json.dumps(
                    {
                        "ok": "false",
                        "stage": "command",
                        "type": "system_error",
                        "summary": "malformed",
                        "sessionId": session_id,
                        "dataJson": json.dumps(
                            {
                                "command": {
                                    "commandNamespace": "command",
                                    "action": "registry.snapshot",
                                    "sessionId": session_id,
                                },
                                "resultJson": "{}",
                            }
                        ),
                    }
                )
                try:
                    return parser(
                        raw,
                        session_id=session_id,
                        mode=current_mode_name(),
                        run_id="run",
                        duration_ms=1,
                    )
                except ValueError as exc:
                    return {
                        "ok": False,
                        "exitCode": 3,
                        "summary": str(exc),
                        "data": {},
                    }

        session = object.__new__(core_bridge.ConsoleSession)
        session._cmd = _CommandProtocol()
        session._post = object()
        session._parser = type(
            "_Parser",
            (),
            {"parse_command_http_response": mock.Mock(return_value={"ok": True})},
        )()
        session._mode_name = lambda: "editor"
        session._session_id = ""
        session._timeout = 10

        result = session.registry_snapshot()

        self.assertFalse(result["ok"])
        self.assertEqual(3, result["exitCode"])
        self.assertIn("status", result["summary"])
        session._parser.parse_command_http_response.assert_not_called()

    def test_registry_operations_use_canonical_control_plane_commands(self):
        session = object.__new__(core_bridge.ConsoleSession)
        session._request_command = mock.Mock(return_value={"ok": True})

        self.assertEqual({"ok": True}, session.registry_snapshot())
        self.assertEqual(
            {"ok": True},
            session.registry_snapshot("generation-token"),
        )

        self.assertEqual(
            [
                mock.call("command", "registry.snapshot"),
                mock.call(
                    "command",
                    "registry.snapshot",
                    {"ifGeneration": "generation-token"},
                ),
            ],
            session._request_command.call_args_list,
        )

    def test_registry_snapshot_rejects_invalid_token_without_dispatch(self):
        session = object.__new__(core_bridge.ConsoleSession)
        session._request_command = mock.Mock(return_value={"ok": True})

        with self.assertRaisesRegex(ValueError, "ifGeneration"):
            session.registry_snapshot("")

        session._request_command.assert_not_called()


class CanonicalBatchBridgeTests(unittest.TestCase):
    def _models(self):
        models = types.ModuleType("csharpconsole_core.models")

        def make_result(
            ok,
            stage,
            result_type,
            exit_code,
            summary,
            session_id,
            mode,
            run_id,
            duration_ms,
            data=None,
        ):
            return {
                "ok": ok,
                "stage": stage,
                "type": result_type,
                "exitCode": exit_code,
                "summary": summary,
                "sessionId": session_id,
                "mode": mode,
                "runId": run_id,
                "durationMs": duration_ms,
                "data": data or {},
            }

        models.make_result = make_result
        models.new_run_id = lambda: "run"
        package = types.ModuleType("csharpconsole_core")
        package.models = models
        return {
            "csharpconsole_core": package,
            "csharpconsole_core.models": models,
        }

    def test_batch_serializes_only_prepared_wire_and_returns_canonical_ids(self):
        result_items = [
            {
                "ok": True,
                "type": "ok",
                "summary": "done",
                "commandNamespace": "internal-only",
                "action": "query",
                "sessionId": "shared",
                "resultJson": '{"value":1}',
            }
        ]
        envelope = {
            "ok": True,
            "stage": "command",
            "type": "ok",
            "summary": "Batch completed",
            "sessionId": "",
            "dataJson": json.dumps(
                {
                    "ok": True,
                    "total": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "resultsJson": json.dumps(result_items),
                }
            ),
        }
        session = object.__new__(core_bridge.ConsoleSession)
        session._session_id = "shared"
        session._mode_name = lambda: "editor"
        posted = []
        session._post = lambda endpoint, payload: (
            posted.append((endpoint, payload)) or json.dumps(envelope)
        )
        prepared = [
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {"name": "Wall"},
            }
        ]

        with mock.patch.dict(sys.modules, self._models()):
            result = session.batch(prepared, stop_on_error=True)

        self.assertEqual(
            [
                (
                    "batch",
                    {
                        "commands": [
                            {
                                "commandNamespace": "internal-only",
                                "action": "query",
                                "sessionId": "shared",
                                "argsJson": '{"name": "Wall"}',
                            }
                        ],
                        "stopOnError": True,
                    },
                )
            ],
            posted,
        )
        self.assertEqual(
            [
                {
                    "index": 0,
                    "id": "public/read",
                    "ok": True,
                    "type": "ok",
                    "summary": "done",
                    "sessionId": "shared",
                    "data": {"value": 1},
                }
            ],
            result["data"]["results"],
        )

    def test_batch_rejects_legacy_wire_aliases_without_posting(self):
        session = object.__new__(core_bridge.ConsoleSession)
        session._session_id = "shared"
        session._mode_name = lambda: "editor"
        session._post = mock.Mock()

        with mock.patch.dict(sys.modules, self._models()):
            result = session.batch(
                [{"ns": "internal-only", "action": "query", "args": {}}],
            )

        self.assertFalse(result["ok"])
        self.assertIn("prepared", result["summary"])
        session._post.assert_not_called()

    def test_batch_rejects_non_boolean_stop_policy_without_posting(self):
        session = object.__new__(core_bridge.ConsoleSession)
        session._session_id = "shared"
        session._mode_name = lambda: "editor"
        session._post = mock.Mock()
        prepared = [
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {},
            }
        ]

        with mock.patch.dict(sys.modules, self._models()):
            result = session.batch(prepared, stop_on_error="false")

        self.assertFalse(result["ok"])
        self.assertIn("stop_on_error", result["summary"])
        session._post.assert_not_called()

    def test_batch_returns_structured_transport_failure(self):
        session = object.__new__(core_bridge.ConsoleSession)
        session._session_id = "shared"
        session._mode_name = lambda: "editor"
        session._post = mock.Mock(side_effect=TimeoutError("response timed out"))
        prepared = [
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {},
            }
        ]

        with mock.patch.dict(sys.modules, self._models()):
            result = session.batch(prepared)

        self.assertFalse(result["ok"])
        self.assertEqual(3, result["exitCode"])
        self.assertIn("timed out", result["summary"])
        session._post.assert_called_once()

    def test_batch_rejects_missing_or_invalid_success_result_json(self):
        malformed_items = (
            {
                "ok": True,
                "type": "ok",
                "summary": "missing",
                "commandNamespace": "internal-only",
                "action": "query",
                "sessionId": "shared",
            },
            {
                "ok": True,
                "type": "ok",
                "summary": "invalid",
                "commandNamespace": "internal-only",
                "action": "query",
                "sessionId": "shared",
                "resultJson": '{"value": ',
            },
        )
        prepared = [
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {},
            }
        ]
        for item in malformed_items:
            envelope = {
                "ok": True,
                "stage": "command",
                "type": "ok",
                "summary": "Batch completed",
                "sessionId": "",
                "dataJson": json.dumps(
                    {
                        "ok": True,
                        "total": 1,
                        "succeeded": 1,
                        "failed": 0,
                        "resultsJson": json.dumps([item]),
                    }
                ),
            }
            session = object.__new__(core_bridge.ConsoleSession)
            session._session_id = "shared"
            session._mode_name = lambda: "editor"
            session._post = lambda endpoint, payload, value=envelope: json.dumps(
                value
            )

            with self.subTest(item=item), mock.patch.dict(
                sys.modules,
                self._models(),
            ):
                result = session.batch(prepared)

            self.assertFalse(result["ok"])
            self.assertEqual(3, result["exitCode"])
            self.assertIn("resultJson", result["summary"])

    def test_batch_rejects_mismatched_session_echo(self):
        item = {
            "ok": True,
            "type": "ok",
            "summary": "done",
            "commandNamespace": "internal-only",
            "action": "query",
            "sessionId": "other-session",
            "resultJson": "{}",
        }
        envelope = {
            "ok": True,
            "stage": "command",
            "type": "ok",
            "summary": "Batch completed",
            "sessionId": "",
            "dataJson": json.dumps(
                {
                    "ok": True,
                    "total": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "resultsJson": json.dumps([item]),
                }
            ),
        }
        session = object.__new__(core_bridge.ConsoleSession)
        session._session_id = "shared"
        session._mode_name = lambda: "editor"
        session._post = lambda endpoint, payload: json.dumps(envelope)
        prepared = [
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {},
            }
        ]

        with mock.patch.dict(sys.modules, self._models()):
            result = session.batch(prepared)

        self.assertFalse(result["ok"])
        self.assertEqual(3, result["exitCode"])
        self.assertIn("session", result["summary"])

    def test_batch_rejects_impossible_stop_on_error_sequence(self):
        items = [
            {
                "ok": False,
                "type": "validation_error",
                "summary": "first failed",
                "commandNamespace": "internal",
                "action": "first",
                "sessionId": "shared",
                "resultJson": "",
            },
            {
                "ok": True,
                "type": "ok",
                "summary": "should not have run",
                "commandNamespace": "internal",
                "action": "second",
                "sessionId": "shared",
                "resultJson": "{}",
            },
        ]
        envelope = {
            "ok": False,
            "stage": "command",
            "type": "system_error",
            "summary": "Batch failed",
            "sessionId": "",
            "dataJson": json.dumps(
                {
                    "ok": False,
                    "total": 2,
                    "succeeded": 1,
                    "failed": 1,
                    "resultsJson": json.dumps(items),
                }
            ),
        }
        session = object.__new__(core_bridge.ConsoleSession)
        session._session_id = "shared"
        session._mode_name = lambda: "editor"
        session._post = lambda endpoint, payload: json.dumps(envelope)
        prepared = [
            {
                "id": "public/first",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal",
                    "action": "first",
                },
                "args": {},
            },
            {
                "id": "public/second",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal",
                    "action": "second",
                },
                "args": {},
            },
            {
                "id": "public/third",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal",
                    "action": "third",
                },
                "args": {},
            },
        ]

        with mock.patch.dict(sys.modules, self._models()):
            result = session.batch(prepared, stop_on_error=True)

        self.assertFalse(result["ok"])
        self.assertEqual(3, result["exitCode"])
        self.assertIn("stopOnError", result["summary"])

    def test_batch_strictly_validates_route_less_request_error(self):
        malformed_data = (
            {
                "ok": "false",
                "total": 0,
                "succeeded": 0,
                "failed": 1,
                "resultsJson": json.dumps(
                    [
                        {
                            "ok": False,
                            "type": "system_error",
                            "summary": "bad request",
                            "commandNamespace": "",
                            "action": "",
                            "sessionId": "",
                            "resultJson": "",
                        }
                    ]
                ),
            },
            {
                "ok": False,
                "total": 0,
                "succeeded": 0,
                "failed": 1,
                "resultsJson": json.dumps(
                    [
                        {
                            "ok": False,
                            "type": "system_error",
                            "summary": "bad request",
                            "commandNamespace": "",
                            "action": "",
                            "sessionId": "",
                        }
                    ]
                ),
            },
            {
                "ok": False,
                "total": 0,
                "succeeded": 1,
                "failed": 1,
                "resultsJson": json.dumps(
                    [
                        {
                            "ok": False,
                            "type": "system_error",
                            "summary": "bad request",
                            "commandNamespace": "",
                            "action": "",
                            "sessionId": "",
                            "resultJson": "",
                        }
                    ]
                ),
            },
        )
        prepared = [
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {},
            }
        ]
        for data in malformed_data:
            envelope = {
                "ok": False,
                "stage": "command",
                "type": "system_error",
                "summary": "Batch failed",
                "sessionId": "",
                "dataJson": json.dumps(data),
            }
            session = object.__new__(core_bridge.ConsoleSession)
            session._session_id = "shared"
            session._mode_name = lambda: "editor"
            session._post = lambda endpoint, payload, value=envelope: json.dumps(
                value
            )

            with self.subTest(data=data), mock.patch.dict(
                sys.modules,
                self._models(),
            ):
                result = session.batch(prepared)

            self.assertFalse(result["ok"])
            self.assertEqual(3, result["exitCode"])
            self.assertTrue(
                "data status" in result["summary"]
                or "resultJson" in result["summary"]
                or "counts" in result["summary"]
            )

    def test_batch_rejects_counts_that_disagree_with_item_statuses(self):
        envelope = {
            "ok": True,
            "stage": "command",
            "type": "ok",
            "summary": "Batch completed",
            "sessionId": "",
            "dataJson": json.dumps(
                {
                    "ok": True,
                    "total": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "resultsJson": json.dumps(
                        [
                            {
                                "ok": False,
                                "type": "validation_error",
                                "summary": "bad",
                                "commandNamespace": "internal-only",
                                "action": "query",
                                "sessionId": "shared",
                                "resultJson": "{}",
                            }
                        ]
                    ),
                }
            ),
        }
        session = object.__new__(core_bridge.ConsoleSession)
        session._session_id = "shared"
        session._mode_name = lambda: "editor"
        session._post = lambda endpoint, payload: json.dumps(envelope)
        prepared = [
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {},
            }
        ]

        with mock.patch.dict(sys.modules, self._models()):
            result = session.batch(prepared)

        self.assertFalse(result["ok"])
        self.assertIn("status counts", result["summary"])

    def test_batch_rejects_non_boolean_envelope_status(self):
        envelope = {
            "ok": "false",
            "summary": "Batch completed",
            "dataJson": json.dumps(
                {
                    "total": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "resultsJson": json.dumps(
                        [
                            {
                                "ok": True,
                                "type": "ok",
                                "summary": "done",
                                "commandNamespace": "internal-only",
                                "action": "query",
                                "sessionId": "shared",
                                "resultJson": "{}",
                            }
                        ]
                    ),
                }
            ),
        }
        session = object.__new__(core_bridge.ConsoleSession)
        session._session_id = "shared"
        session._mode_name = lambda: "editor"
        session._post = lambda endpoint, payload: json.dumps(envelope)
        prepared = [
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {},
            }
        ]

        with mock.patch.dict(sys.modules, self._models()):
            result = session.batch(prepared)

        self.assertFalse(result["ok"])
        self.assertEqual(3, result["exitCode"])
        self.assertIn("envelope status", result["summary"])

    def test_batch_rejects_status_type_mismatch(self):
        prepared = [
            {
                "id": "public/read",
                "partition": "custom",
                "wire": {
                    "commandNamespace": "internal-only",
                    "action": "query",
                },
                "args": {},
            }
        ]
        mismatches = (
            ("system_error", "ok"),
            ("ok", "system_error"),
        )
        for envelope_type, item_type in mismatches:
            item = {
                "ok": True,
                "type": item_type,
                "summary": "done",
                "commandNamespace": "internal-only",
                "action": "query",
                "sessionId": "shared",
                "resultJson": "{}",
            }
            envelope = {
                "ok": True,
                "stage": "command",
                "type": envelope_type,
                "summary": "Batch completed",
                "sessionId": "",
                "dataJson": json.dumps(
                    {
                        "ok": True,
                        "total": 1,
                        "succeeded": 1,
                        "failed": 0,
                        "resultsJson": json.dumps([item]),
                    }
                ),
            }
            session = object.__new__(core_bridge.ConsoleSession)
            session._session_id = "shared"
            session._mode_name = lambda: "editor"
            session._post = lambda endpoint, payload, value=envelope: json.dumps(
                value
            )

            with self.subTest(
                envelope_type=envelope_type,
                item_type=item_type,
            ), mock.patch.dict(sys.modules, self._models()):
                result = session.batch(prepared)

            self.assertFalse(result["ok"])
            self.assertEqual(3, result["exitCode"])
            self.assertIn("type", result["summary"])


def _prepared(command_id, namespace, action):
    return {
        "id": command_id,
        "partition": "builtin",
        "wire": {"commandNamespace": namespace, "action": action},
        "args": {},
    }


class _RuntimeState:
    """A runtime-mode SharedConfigState stand-in."""

    def __init__(self, runtime_mode=True, compile_ip="127.0.0.1", compile_port=14501):
        self.runtime_mode = runtime_mode
        self.runtime_ip = "10.0.0.5"
        self.runtime_port = 15503
        # Normalized by ConsoleSession.__init__ before anything reads them, so
        # a stand-in that left them unset would be a shape the code never sees.
        self.compile_ip = compile_ip
        self.compile_port = compile_port

    def current_server_base_url(self):
        return "http://127.0.0.1:14501/CSharpConsole"


def _core_models():
    """The csharpconsole_core.models stand-in every bridge test builds on."""
    module = types.ModuleType("csharpconsole_core.models")
    module.make_result = lambda ok, stage, result_type, exit_code, summary, *rest: {
        "ok": ok,
        "type": result_type,
        "exitCode": exit_code,
        "summary": summary,
        "data": rest[-1] if rest and isinstance(rest[-1], dict) else {},
    }
    module.new_run_id = lambda: "run-1"
    return module


class _RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def post_json(self, url_base, endpoint, payload, timeout):
        self.urls.append(url_base)
        return self.response


class RuntimeAddressingTests(unittest.TestCase):
    """Which process each call is addressed to in runtime mode.

    csharpconsole_core sends every endpoint but execute to the compile server,
    so a path that forgets to say "the player answers this" silently runs
    against the editor instead -- returning the editor's answer to a question
    asked about the player.
    """

    PLAYER_URL = "http://10.0.0.5:15503/CSharpConsole"
    EDITOR_URL = "http://127.0.0.1:14501/CSharpConsole"

    def _models(self):
        module = _core_models()
        package = types.ModuleType("csharpconsole_core")
        package.models = module
        return {"csharpconsole_core": package, "csharpconsole_core.models": module}

    def _session(self, response, runtime_mode=True):
        session = object.__new__(core_bridge.ConsoleSession)
        session._session_id = "shared"
        session._mode_name = lambda: "runtime" if runtime_mode else "editor"
        session._timeout = 10
        session._state = _RuntimeState(runtime_mode)
        transport = _RecordingTransport(response)
        session._post = core_bridge._make_post_with_retry(
            transport, session._state, 10, session._base_url
        )
        return session, transport

    def _batch_envelope(self, count):
        results = [
            {
                "ok": True,
                "type": "",
                "summary": "done",
                "sessionId": "shared",
                "commandNamespace": "runtime",
                "action": "info",
                "resultJson": "{}",
            }
            for _ in range(count)
        ]
        return json.dumps(
            {
                "ok": True,
                "type": "",
                "summary": f"Batch: {count}/{count} succeeded",
                "sessionId": "",
                "dataJson": json.dumps(
                    {
                        "ok": True,
                        "total": count,
                        "succeeded": count,
                        "failed": 0,
                        "resultsJson": json.dumps(results),
                    }
                ),
            }
        )

    def test_batch_is_addressed_to_the_player_in_runtime_mode(self):
        session, transport = self._session(self._batch_envelope(1))
        prepared = [_prepared("runtime/info", "runtime", "info")]

        with mock.patch.dict(sys.modules, self._models()):
            session.batch(prepared)

        self.assertEqual([self.PLAYER_URL], transport.urls)

    def test_batch_stays_with_the_editor_outside_runtime_mode(self):
        session, transport = self._session(self._batch_envelope(1), runtime_mode=False)
        prepared = [_prepared("runtime/info", "runtime", "info")]

        with mock.patch.dict(sys.modules, self._models()):
            session.batch(prepared)

        self.assertEqual([self.EDITOR_URL], transport.urls)

    def test_batch_refuses_registry_owning_commands_in_runtime_mode(self):
        session, transport = self._session(self._batch_envelope(2))
        prepared = [
            _prepared("runtime/info", "runtime", "info"),
            _prepared("command/list", "command", "list"),
        ]

        with mock.patch.dict(sys.modules, self._models()):
            result = session.batch(prepared)

        self.assertFalse(result["ok"])
        self.assertIn("command/list", result["summary"])
        self.assertEqual([], transport.urls)

    def test_batch_clears_the_player_flag_when_the_post_fails(self):
        session, transport = self._session(self._batch_envelope(1))
        session._post = mock.Mock(side_effect=RuntimeError("boom"))
        prepared = [_prepared("runtime/info", "runtime", "info")]

        with mock.patch.dict(sys.modules, self._models()):
            session.batch(prepared)

        self.assertFalse(session._answered_by_player)
        self.assertEqual(self.EDITOR_URL, session._base_url())


class _CompileSetClient:
    """A client_base stand-in recording what the bridge hands the core."""

    TIMEOUT_ZIP_TRANSFER = 600

    def __init__(self, response=None):
        self.calls = []
        self.response = response or {
            "ok": True, "exitCode": 0, "summary": "Registered", "data": {},
        }

    def request_compile_set(self, post_binary, parse, base_url, ip, port, zip_bytes):
        self.calls.append({
            "base_url": base_url,
            "ip": ip,
            "port": port,
            "zip_bytes": zip_bytes,
            # The core reads a skip off zip_bytes being None; there is no
            # separate flag to record.
            "skip": zip_bytes is None,
        })
        return self.response


class CompileSetBridgeTests(unittest.TestCase):
    """Reading the compile set is ours; the core only ever sees bytes.

    So a zip that is not there, or a URL that will not download, has to become
    a result here rather than an exception escaping into the CLI.
    """

    def _session(self, response=None):
        session = object.__new__(core_bridge.ConsoleSession)
        session._state = _RuntimeState()
        session._client = _CompileSetClient(response)
        session._mode_name = lambda: "runtime"
        session._parser = types.SimpleNamespace(
            parse_compile_set_http_response=lambda *a: None
        )
        return session

    def _modules(self, read_compile_set, get_bytes=None):
        artifacts = types.ModuleType("csharpconsole_core.runtime_artifacts_base")
        artifacts.read_compile_set = read_compile_set
        transport = types.ModuleType("csharpconsole_core.transport_http")
        transport.get_bytes = get_bytes or (lambda url, timeout: b"")
        transport.post_binary = lambda url, body, timeout: ""
        models = _core_models()
        package = types.ModuleType("csharpconsole_core")
        package.runtime_artifacts_base = artifacts
        package.transport_http = transport
        package.models = models
        return {
            "csharpconsole_core": package,
            "csharpconsole_core.runtime_artifacts_base": artifacts,
            "csharpconsole_core.transport_http": transport,
            "csharpconsole_core.models": models,
        }

    def test_the_zip_goes_to_the_editor_naming_the_player(self):
        session = self._session()
        modules = self._modules(lambda source, fetch: b"ZIPBYTES")

        with mock.patch.dict(sys.modules, modules):
            result = session.compile_set("set.zip")

        self.assertTrue(result["ok"])
        self.assertEqual(
            [{
                "base_url": "http://127.0.0.1:14501/CSharpConsole",
                "ip": "10.0.0.5",
                "port": 15503,
                "zip_bytes": b"ZIPBYTES",
                "skip": False,
            }],
            session._client.calls,
        )

    def test_a_url_is_downloaded_on_the_compile_set_budget(self):
        downloads = []
        session = self._session()

        def get_bytes(url, timeout):
            downloads.append((url, timeout))
            return b"ZIP"

        modules = self._modules(lambda source, fetch: fetch(source), get_bytes=get_bytes)

        with mock.patch.dict(sys.modules, modules):
            session.compile_set("https://example.test/set.zip")

        self.assertEqual([("https://example.test/set.zip", 600)], downloads)
        self.assertEqual(b"ZIP", session._client.calls[0]["zip_bytes"])

    def test_skipping_sends_no_zip_and_reads_nothing(self):
        session = self._session()

        def unreadable(source, fetch):
            raise AssertionError("skip must not read a compile set")

        with mock.patch.dict(sys.modules, self._modules(unreadable)):
            session.compile_set(None, skip=True)

        call = session._client.calls[0]
        self.assertIsNone(call["zip_bytes"])
        self.assertTrue(call["skip"])

    def test_a_missing_zip_is_the_callers_error_and_never_posts(self):
        session = self._session()

        def missing(source, fetch):
            raise FileNotFoundError(f"Compile set zip not found: {source}")

        with mock.patch.dict(sys.modules, self._modules(missing)):
            result = session.compile_set("nope.zip")

        self.assertFalse(result["ok"])
        self.assertEqual("validation_error", result["type"])
        self.assertEqual(1, result["exitCode"])
        self.assertIn("nope.zip", result["summary"])
        self.assertEqual([], session._client.calls)

    def test_a_failed_download_is_a_system_error_and_never_posts(self):
        session = self._session()

        def refused(source, fetch):
            raise _Transport.TransportError("HTTP 404 Not Found")

        with mock.patch.dict(sys.modules, self._modules(refused)):
            result = session.compile_set("https://example.test/set.zip")

        self.assertFalse(result["ok"])
        self.assertEqual("system_error", result["type"])
        self.assertEqual(3, result["exitCode"])
        self.assertIn("HTTP 404 Not Found", result["summary"])
        self.assertEqual([], session._client.calls)
        # A 404 from the URL the user gave is not the editor missing an
        # endpoint, and must not be answered with "align the package".
        self.assertNotIn("/compile-set endpoint", result["summary"])

    def test_a_404_from_the_editor_names_the_endpoint_it_lacks(self):
        session = self._session({
            "ok": False, "exitCode": 3, "data": {},
            "summary": "Compile set request failed: HTTP 404 Not Found",
        })

        with mock.patch.dict(sys.modules, self._modules(lambda source, fetch: b"ZIP")):
            result = session.compile_set("set.zip")

        self.assertIn("cs setup --update", result["summary"])


class AlignmentGuidanceTests(unittest.TestCase):
    """What an agent is told when a player build has no compile-set decision.

    The service names the build it is asking about but cannot know how this
    caller reached the player, so the bridge supplies the runnable command —
    and says whose decision skipping is. It hangs off exec's runtime branch,
    so every caller of session.exec gets it, not just `cs exec`.
    """

    STATE = _RuntimeState(compile_ip="10.0.0.9", compile_port=14502)

    def _refusal(self):
        text = f"{core_bridge.ALIGNMENT_REQUIRED_MARKER}\nThis player is build b1ab0ce5."
        return {"ok": False, "exitCode": 1, "summary": text, "data": {"text": text}}

    # Both halves of the address: registering with a re-detected local editor
    # instead of the one this call compiled against changes nothing, and the
    # next submission is refused again.
    ADDRESS = "--ip 10.0.0.5 --port 15503 --compile-ip 10.0.0.9 --compile-port 14502"

    def test_both_channels_carry_the_command_that_answers_it(self):
        result = core_bridge._with_alignment_guidance(self._refusal(), self.STATE)

        for channel in (result["summary"], result["data"]["text"]):
            self.assertIn("b1ab0ce5", channel)
            self.assertIn(f"cs compile-set <zip|URL> {self.ADDRESS}", channel)
            self.assertIn(f"cs compile-set --skip {self.ADDRESS}", channel)
        self.assertIn("skip on their behalf", result["summary"])

    def test_the_editor_is_named_even_when_it_was_never_stated(self):
        # ConsoleSession normalizes an unset --compile-ip to the local editor
        # before anything reads it, so the printed command is runnable rather
        # than carrying a None where the address belongs.
        result = core_bridge._with_alignment_guidance(self._refusal(), _RuntimeState())

        self.assertIn("--compile-ip 127.0.0.1 --compile-port 14501", result["summary"])
        self.assertNotIn("None", result["summary"])

    def test_every_other_outcome_is_left_alone(self):
        untouched = (
            {"ok": False, "exitCode": 2, "summary": "C# Exception: boom", "data": {"text": "x"}},
            {"ok": True, "exitCode": 0, "summary": "42", "data": {"text": "42"}},
        )
        for result in untouched:
            with self.subTest(summary=result["summary"]):
                guided = core_bridge._with_alignment_guidance(result, self.STATE)
                self.assertEqual(result["summary"], guided["summary"])
                self.assertEqual(result["data"]["text"], guided["data"]["text"])

    def test_an_editor_submission_never_reaches_it(self):
        # Editor mode has no player and no compile target, so a command naming
        # one would address the wrong process. exec's editor branch skips it.
        session = object.__new__(core_bridge.ConsoleSession)
        session._state = _RuntimeState(runtime_mode=False)
        session._session_id = "s"
        session._post = None
        session._define = session._using = lambda: ""
        refusal = self._refusal()
        session._parser = types.SimpleNamespace(parse_text_http_response=None)
        session._client = types.SimpleNamespace(
            execute_editor_request=lambda *a, **k: refusal,
        )

        self.assertEqual(refusal["summary"], session.exec("1+1")["summary"])


if __name__ == "__main__":
    unittest.main()
