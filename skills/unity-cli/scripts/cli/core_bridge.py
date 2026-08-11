"""Dynamic bridge to csharpconsole_core from an installed Unity package."""

import errno
import json
import math
import os
import sys
import time
from pathlib import Path

from cli import PACKAGE_NAME, DEFAULT_EDITOR_PORT, load_pkg_path, save_pkg_path

CORE_RELATIVE = Path("Editor/ExternalTool~/console-client")
_RETRY_DELAY_S = 1
_COMMAND_EXIT_CODES = {
    "compile_error": 1,
    "runtime_error": 2,
    "system_error": 3,
    "validation_error": 1,
}


def _is_connection_refused(error):
    """Return True only when a request clearly failed before connecting.

    Timeouts and resets can occur after Unity has applied a mutation. Retrying
    either would risk applying one command or an entire batch twice.
    """
    pending = [error]
    seen = set()
    refused_codes = {errno.ECONNREFUSED, 10061}

    def is_refused_code(value):
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value in refused_codes
        )

    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        if isinstance(current, ConnectionRefusedError):
            return True
        if is_refused_code(getattr(current, "errno", None)):
            return True
        if is_refused_code(getattr(current, "winerror", None)):
            return True

        cause = getattr(current, "__cause__", None)
        reason = getattr(current, "reason", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, BaseException):
            pending.append(cause)
        elif isinstance(reason, BaseException):
            pending.append(reason)
        elif (
            not getattr(current, "__suppress_context__", False)
            and isinstance(context, BaseException)
        ):
            pending.append(context)

    return False


def _find_pkg_dir(project_root):
    """Locate the package root directory. Returns (pkg_dir, core_path) or (None, None)."""
    root = Path(project_root)

    # 1. manifest.json file: entry
    try:
        deps = json.loads((root / "Packages" / "manifest.json").read_text("utf-8")).get("dependencies", {})
    except (json.JSONDecodeError, OSError):
        deps = {}
    value = deps.get(PACKAGE_NAME, "")
    if value.startswith("file:"):
        # file: paths are relative to the Packages/ folder (where manifest.json lives)
        pkg_dir = (root / "Packages" / value[len("file:"):]).resolve()
        candidate = pkg_dir / CORE_RELATIVE
        if (candidate / "csharpconsole_core").is_dir():
            return pkg_dir, candidate

    # 2. Unity package cache (git-installed packages)
    cache_dir = root / "Library" / "PackageCache"
    if cache_dir.is_dir():
        for d in cache_dir.iterdir():
            if d.name == PACKAGE_NAME or d.name.startswith(PACKAGE_NAME + "@"):
                candidate = d / CORE_RELATIVE
                if (candidate / "csharpconsole_core").is_dir():
                    return d, candidate

    return None, None


def find_package_dir(project_root, agent_root=None):
    """Return the package root directory, or None."""
    if agent_root:
        cached_pkg = load_pkg_path(agent_root)
        if cached_pkg and (cached_pkg / CORE_RELATIVE / "csharpconsole_core").is_dir():
            return cached_pkg
    pkg_dir, _ = _find_pkg_dir(project_root)
    if pkg_dir:
        if agent_root:
            save_pkg_path(agent_root, pkg_dir)
        return pkg_dir
    return None


def resolve(project_root, agent_root=None):
    """Find the csharpconsole_core directory. Returns Path or raises FileNotFoundError."""
    pkg_dir = find_package_dir(project_root, agent_root)
    if pkg_dir:
        return pkg_dir / CORE_RELATIVE
    raise FileNotFoundError(
        f"csharpconsole_core not found in {project_root}. Run 'cs setup' first."
    )


def is_available(project_root, agent_root=None):
    return find_package_dir(project_root, agent_root) is not None


def _ensure_path(core_path):
    """Add core_path and its site-packages to sys.path if needed."""
    s = str(core_path)
    if s not in sys.path:
        sys.path.insert(0, s)
    sp = os.path.join(s, "site-packages")
    if os.path.isdir(sp) and sp not in sys.path:
        sys.path.insert(0, sp)


def _make_post_with_retry(transport_http, state, default_timeout):
    """Create a POST function that retries one refused connection."""
    # The urllib-based core raises TransportError for every transport failure
    # (connection refused, timeout, non-2xx). Older requests-based cores raised
    # OSError subclasses instead. Catch both so the domain-reload retry survives
    # whichever core version is resolved; fall back to OSError only against a
    # core that predates TransportError.
    transport_error = getattr(transport_http, "TransportError", None)
    transport_failures = (
        (OSError, transport_error)
        if transport_error is not None
        else (OSError,)
    )

    def _post(endpoint, payload, timeout=None):
        t = timeout if timeout is not None else default_timeout
        url_base = state.current_server_base_url()
        try:
            return transport_http.post_json(url_base, endpoint, payload, t)
        except transport_failures as error:
            if not _is_connection_refused(error):
                raise
            time.sleep(_RETRY_DELAY_S)
            return transport_http.post_json(url_base, endpoint, payload, t)

    return _post


def _prepared_command_parts(prepared):
    """Return canonical id, wire route, and args from one preflight result."""
    if not isinstance(prepared, dict) or set(prepared) != {
        "id",
        "partition",
        "wire",
        "args",
    }:
        raise ValueError("expected a canonical prepared command")
    command_id = prepared["id"]
    partition = prepared["partition"]
    wire = prepared["wire"]
    args = prepared["args"]
    if not isinstance(command_id, str) or not command_id:
        raise ValueError("prepared command id must be a non-empty string")
    if partition not in {"builtin", "custom"}:
        raise ValueError("prepared command partition must be builtin or custom")
    if not isinstance(wire, dict) or set(wire) != {
        "commandNamespace",
        "action",
    }:
        raise ValueError("prepared command wire must contain exact route fields")
    namespace = wire["commandNamespace"]
    action = wire["action"]
    if not isinstance(namespace, str) or not isinstance(action, str):
        raise ValueError("prepared command wire route must use strings")
    if not isinstance(args, dict):
        raise ValueError("prepared command args must be an object")
    return command_id, namespace, action, args


def _keep_json_constant(value):
    """Keep a non-finite literal as text so the result stays readable JSON.

    Unity serializes float.NaN and float.Infinity as bare JSON constants, and a
    corrupted Transform is exactly the state worth inspecting. Dropping the
    whole result would hide it; re-emitting the raw constant would produce
    output the agent cannot parse.
    """
    return value


def _parse_json_float(value):
    """Keep a literal that overflows a double as its own text.

    Only parse_constant sees Unity's bare non-finite tokens, so anything
    reaching here is a real numeric literal and must not be relabelled as one
    of them.
    """
    parsed = float(value)
    return parsed if math.isfinite(parsed) else value


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _load_json_strict(raw, label):
    if not isinstance(raw, str):
        raise ValueError(f"Invalid {label}: expected a JSON string")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_unique_json_object,
            parse_constant=_keep_json_constant,
            parse_float=_parse_json_float,
        )
    except (json.JSONDecodeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid {label}: {exc}") from exc


def _validate_status_type(ok, result_type, label):
    if ok:
        if result_type != "ok":
            raise ValueError(f"Invalid {label} type for successful status")
        return
    if not result_type or result_type == "ok":
        raise ValueError(f"Invalid {label} type for failed status")


def _validate_envelope(envelope, label):
    if not isinstance(envelope, dict):
        raise ValueError(f"Invalid {label}: expected an object")
    if not isinstance(envelope.get("ok"), bool):
        raise ValueError(f"Invalid {label} status")
    for field in ("stage", "type", "summary", "sessionId", "dataJson"):
        if not isinstance(envelope.get(field), str):
            raise ValueError(f"Invalid {label} field {field}")
    if envelope["stage"] != "command":
        raise ValueError(f"Invalid {label} stage")
    _validate_status_type(envelope["ok"], envelope["type"], label)


def _validate_result_json(raw, ok, label):
    if not isinstance(raw, str):
        raise ValueError(f"Invalid {label}: expected a string")
    if not raw.strip():
        if ok:
            raise ValueError(f"Invalid {label}: successful result is empty")
        return {}
    return _load_json_strict(raw, label)


def _validate_batch_result_item(item, index):
    label = f"batch result item {index}"
    if not isinstance(item, dict):
        raise ValueError(f"Invalid {label}: expected an object")
    if not isinstance(item.get("ok"), bool):
        raise ValueError(f"Invalid {label} status")
    for field in (
        "type",
        "summary",
        "commandNamespace",
        "action",
        "sessionId",
        "resultJson",
    ):
        if not isinstance(item.get(field), str):
            raise ValueError(f"Invalid {label} field {field}")
    _validate_status_type(item["ok"], item["type"], label)
    return _validate_result_json(
        item["resultJson"],
        item["ok"],
        f"{label} resultJson",
    )


def _routed_echo(echo, ok):
    """Return True when the echoed route must match the request.

    The package clears every route field when it answers without an invocation,
    which it can only legitimately do on a failure.
    """
    if ok:
        return True
    return any(echo.get(field) for field in ("commandNamespace", "action"))


def _parse_command_http_response_strict(
    delegate,
    raw,
    session_id,
    mode,
    run_id,
    duration_ms,
    *,
    expected_namespace,
    expected_action,
    expected_session_id,
):
    """Validate the raw package envelope before its permissive parser runs."""
    envelope = _load_json_strict(raw, "command response")
    _validate_envelope(envelope, "command response")
    data = _load_json_strict(envelope["dataJson"], "command dataJson")
    if not isinstance(data, dict):
        raise ValueError("Invalid command dataJson: expected an object")
    command = data.get("command")
    if not isinstance(command, dict):
        raise ValueError("Invalid command dataJson field command")
    for field in ("commandNamespace", "action", "sessionId"):
        if not isinstance(command.get(field), str):
            raise ValueError(f"Invalid command route field {field}")
    # A failed response may carry no route at all: the package answers with an
    # empty invocation when it could not parse the request into a command. That
    # answer holds the only diagnostic there is, so let it through rather than
    # replacing it with a route complaint.
    if _routed_echo(command, envelope["ok"]):
        if (
            command["commandNamespace"] != expected_namespace
            or command["action"] != expected_action
        ):
            raise ValueError(
                "Command response wire route did not match the request"
            )
        if (
            command["sessionId"] != expected_session_id
            or envelope["sessionId"] != expected_session_id
        ):
            raise ValueError("Command response session did not match the request")
    _validate_result_json(
        data.get("resultJson"),
        envelope["ok"],
        "command resultJson",
    )
    return delegate(
        raw,
        session_id=session_id,
        mode=mode,
        run_id=run_id,
        duration_ms=duration_ms,
    )


def _protocol_error(result, command_id, summary):
    normalized = dict(result) if isinstance(result, dict) else {}
    normalized.update(
        {
            "ok": False,
            "stage": "command",
            "type": "system_error",
            "exitCode": 3,
            "summary": summary,
            "id": command_id,
            "data": {},
        }
    )
    return normalized


def _normalize_command_result(result, command_id, namespace, action):
    """Attach a canonical id only after validating the parsed command result."""
    if not isinstance(result, dict):
        return _protocol_error(result, command_id, "Invalid command response")

    ok = result.get("ok")
    exit_code = result.get("exitCode")
    result_type = result.get("type")
    if (
        not isinstance(ok, bool)
        or not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or not isinstance(result_type, str)
        or (
            "stage" in result
            and result.get("stage") != "command"
        )
    ):
        return _protocol_error(
            result,
            command_id,
            "Invalid command response status",
        )

    try:
        _validate_status_type(ok, result_type, "command response")
    except ValueError as exc:
        return _protocol_error(result, command_id, str(exc))
    expected_exit_code = (
        0
        if ok
        else _COMMAND_EXIT_CODES.get(result_type, 3)
    )
    if exit_code != expected_exit_code:
        return _protocol_error(
            result,
            command_id,
            "Invalid command response status",
        )

    data = result.get("data")
    if not isinstance(data, dict) or "resultJson" not in data:
        # The package client reports transport and parser failures as a valid
        # system_error without command data. Preserve those diagnostics.
        if not ok:
            normalized = dict(result)
            normalized["id"] = command_id
            normalized["data"] = data if isinstance(data, dict) else {}
            return normalized
        details = result.get("summary")
        suffix = f": {details}" if details else ""
        return _protocol_error(
            result,
            command_id,
            f"Invalid command response payload{suffix}",
        )

    echo = data.get("command")
    if not isinstance(echo, dict):
        return _protocol_error(
            result,
            command_id,
            "Command response is missing its wire route",
        )
    if _routed_echo(echo, ok) and (
        echo.get("commandNamespace") != namespace
        or echo.get("action") != action
    ):
        return _protocol_error(
            result,
            command_id,
            "Command response wire route did not match the prepared command",
        )

    normalized = dict(result)
    normalized["id"] = command_id
    decoded_result = data["resultJson"]
    if not ok and decoded_result == "":
        decoded_result = {}
    normalized["data"] = decoded_result
    return normalized


class ConsoleSession:
    """Pre-wired facade over csharpconsole_core. One-liner per command."""

    def __init__(self, project_root, ip="127.0.0.1", port=DEFAULT_EDITOR_PORT, mode="editor", timeout=30,
                 agent_root=None, pkg_dir=None,
                 compile_ip=None, compile_port=None, session_id=None):
        core_path = (pkg_dir / CORE_RELATIVE) if pkg_dir else resolve(project_root, agent_root)
        _ensure_path(core_path)

        from csharpconsole_core import (
            client_base, command_protocol, config_base,
            output, response_parser, transport_http,
        )
        self._client = client_base
        self._cmd = command_protocol
        self._parser = response_parser
        self._output = output

        state = config_base.SharedConfigState()
        state.ip = ip
        state.port = port
        state.runtime_mode = mode == "runtime"
        if state.runtime_mode:
            # Runtime execution targets the player; compile/refresh/health
            # still go through the editor.
            state.runtime_ip = ip
            state.runtime_port = port
            state.compile_ip = compile_ip or "127.0.0.1"
            state.compile_port = compile_port or DEFAULT_EDITOR_PORT
        self._state = state

        self._session_id = client_base.generate_session_id(session_id)
        self._timeout = timeout
        self._post = _make_post_with_retry(transport_http, state, timeout)
        self._mode_name = lambda: state.current_mode_name()
        # Placeholders required by csharpconsole_core API for persistent
        # using/define directives. Empty for CLI usage; the interactive REPL
        # populates these from DefaultUsing.cs / Defines.txt files.
        self._define = lambda: ""
        self._using = lambda: ""

    def exec(self, code, reset=False):
        # In runtime mode, the snippet must be compiled by the editor and
        # forwarded to the player — execute_runtime_request POSTs to the
        # "compile" endpoint with targetIP/targetPort. Without this branch
        # we'd POST to "editor" and silently run in the local editor.
        if self._state.runtime_mode:
            return self._client.execute_runtime_request(
                self._post, self._parser.parse_text_http_response,
                self._define, self._using,
                self._state.runtime_ip, self._state.runtime_port,
                self._state.runtime_dll_path,
                code, self._session_id, reset,
            )
        return self._client.execute_editor_request(
            self._post, self._parser.parse_text_http_response,
            self._define, self._using, code, self._session_id, reset,
        )

    def _request_command(self, namespace, action, args=None):
        def parse_strict(raw, session_id, mode, run_id, duration_ms):
            return _parse_command_http_response_strict(
                self._parser.parse_command_http_response,
                raw,
                session_id,
                mode,
                run_id,
                duration_ms,
                expected_namespace=namespace,
                expected_action=action,
                expected_session_id=self._session_id,
            )

        return self._cmd.request_command(
            self._post, parse_strict,
            self._mode_name, namespace, action, self._session_id, args,
            timeout_seconds=self._timeout,
        )

    def command(self, prepared):
        """Execute one preflighted canonical command."""
        command_id, namespace, action, args = _prepared_command_parts(prepared)
        result = self._request_command(namespace, action, args)
        return _normalize_command_result(
            result,
            command_id,
            namespace,
            action,
        )

    def health(self):
        return self._client.request_health(
            self._post, self._parser.parse_health_http_response, self._mode_name,
        )

    def refresh(self, exit_playmode=False, changed_files=None):
        payload = {}
        if exit_playmode:
            payload["exitPlayModeIfNeeded"] = True
        if changed_files:
            payload["changedFiles"] = changed_files

        if not payload:
            return self._client.request_refresh(
                self._post, self._parser.parse_refresh_http_response, self._mode_name,
            )

        from csharpconsole_core.models import make_result, new_run_id
        start = time.time()
        run_id = new_run_id()
        try:
            raw = self._post("refresh", payload)
            return self._parser.parse_refresh_http_response(
                raw, self._mode_name(), run_id, (time.time() - start) * 1000,
            )
        except Exception as e:
            return make_result(
                False, "bootstrap", "system_error", 3,
                f"Refresh request failed: {e}", "",
                self._mode_name(), run_id, (time.time() - start) * 1000,
            )

    def wait_ready(self, timeout=60):
        return self._client.wait_for_service_recovery(
            self.health, self._mode_name, timeout,
        )

    def registry_snapshot(self, if_generation=None):
        """Fetch the package-owned registry snapshot, conditional on a token."""
        if if_generation is None:
            return self._request_command("command", "registry.snapshot")
        if not isinstance(if_generation, str) or not if_generation:
            raise ValueError(
                "registry snapshot ifGeneration must be a non-empty string"
            )
        return self._request_command(
            "command",
            "registry.snapshot",
            {"ifGeneration": if_generation},
        )

    def batch(self, prepared_commands, stop_on_error=False):
        """Execute preflighted canonical commands in one HTTP roundtrip."""
        from csharpconsole_core.models import make_result, new_run_id

        start = time.time()
        run_id = new_run_id()

        def failure(summary, result_type="validation_error", exit_code=1):
            return make_result(
                False,
                "command",
                result_type,
                exit_code,
                summary,
                "",
                self._mode_name(),
                run_id,
                (time.time() - start) * 1000,
            )

        if not isinstance(prepared_commands, list) or not prepared_commands:
            return failure("Expected a non-empty array of prepared commands")
        if not isinstance(stop_on_error, bool):
            return failure("stop_on_error must be a boolean")

        canonical = []
        items = []
        try:
            for prepared in prepared_commands:
                command_id, namespace, action, args = (
                    _prepared_command_parts(prepared)
                )
                canonical.append((command_id, namespace, action))
                items.append(
                    {
                        "commandNamespace": namespace,
                        "action": action,
                        "sessionId": self._session_id,
                        "argsJson": json.dumps(
                            args,
                            ensure_ascii=False,
                            allow_nan=False,
                        ),
                    }
                )
        except ValueError as exc:
            return failure(f"Invalid prepared batch command: {exc}")

        payload = {"commands": items, "stopOnError": stop_on_error}
        try:
            raw = self._post("batch", payload)
            envelope = _load_json_strict(raw, "batch response")
            _validate_envelope(envelope, "batch envelope")
            if envelope["sessionId"]:
                raise ValueError("Invalid batch envelope session")
            if not envelope["ok"] and envelope["type"] != "system_error":
                raise ValueError(
                    "Invalid batch envelope type for failed status"
                )

            data = _load_json_strict(
                envelope["dataJson"],
                "batch dataJson",
            )
            if not isinstance(data, dict):
                raise ValueError(
                    "Invalid batch dataJson: expected an object"
                )
            if not isinstance(data.get("ok"), bool):
                raise ValueError("Invalid batch data status")
            if data["ok"] != envelope["ok"]:
                raise ValueError(
                    "Inconsistent batch envelope and data status"
                )
            results_list = _load_json_strict(
                data.get("resultsJson"),
                "batch resultsJson",
            )
            if not isinstance(results_list, list):
                raise ValueError(
                    "Invalid batch resultsJson: expected an array"
                )
            decoded_results = [
                _validate_batch_result_item(item, index)
                for index, item in enumerate(results_list)
            ]

            counts = {
                name: data.get(name)
                for name in ("total", "succeeded", "failed")
            }
            if any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in counts.values()
            ):
                raise ValueError("Invalid batch result counts")
            total = counts["total"]
            succeeded = counts["succeeded"]
            failed = counts["failed"]

            # The package uses a single route-less result for errors raised
            # before any requested command can be associated with the failure.
            if (
                total == 0
                and succeeded == 0
                and failed == 1
                and envelope["ok"] is False
                and data["ok"] is False
                and len(results_list) == 1
                and results_list[0]["ok"] is False
                and results_list[0]["type"] == "system_error"
                and not results_list[0]["commandNamespace"]
                and not results_list[0]["action"]
                and not results_list[0]["sessionId"]
                and not results_list[0]["resultJson"]
            ):
                request_error = results_list[0]
                return make_result(
                    False,
                    "command",
                    "system_error",
                    3,
                    request_error["summary"]
                    or envelope["summary"]
                    or "Batch request failed",
                    "",
                    self._mode_name(),
                    run_id,
                    (time.time() - start) * 1000,
                    {
                        "total": 0,
                        "succeeded": 0,
                        "failed": 1,
                        "results": [
                            {
                                "ok": False,
                                "type": "system_error",
                                "summary": request_error["summary"],
                                "sessionId": "",
                                "data": {},
                            }
                        ],
                    },
                )

            if (
                succeeded + failed != total
                or total != len(results_list)
                or total > len(canonical)
                or (not stop_on_error and total != len(canonical))
                or (
                    stop_on_error
                    and total < len(canonical)
                    and failed == 0
                )
            ):
                raise ValueError("Inconsistent batch response counts")

            normalized_results = []
            for index, item in enumerate(results_list):
                command_id, namespace, action = canonical[index]
                if (
                    item["commandNamespace"] != namespace
                    or item["action"] != action
                ):
                    raise ValueError(
                        f"Batch result item {index} wire route mismatch"
                    )
                if item["sessionId"] != self._session_id:
                    raise ValueError(
                        f"Batch result item {index} session mismatch"
                    )
                normalized_results.append(
                    {
                        "index": index,
                        "id": command_id,
                        "ok": item["ok"],
                        "type": item["type"],
                        "summary": item["summary"],
                        "sessionId": item["sessionId"],
                        "data": decoded_results[index],
                    }
                )

            actual_succeeded = sum(
                1 for item in normalized_results if item["ok"]
            )
            if (
                actual_succeeded != succeeded
                or total - actual_succeeded != failed
            ):
                raise ValueError(
                    "Inconsistent batch item status counts"
                )
            if (
                stop_on_error
                and failed
                and (
                    failed != 1
                    or not normalized_results
                    or normalized_results[-1]["ok"]
                    or any(
                        not item["ok"]
                        for item in normalized_results[:-1]
                    )
                )
            ):
                raise ValueError("Invalid stopOnError result sequence")

            ok = envelope["ok"]
            if ok != (failed == 0):
                raise ValueError("Inconsistent batch response status")
            return make_result(
                ok,
                "command",
                "" if ok else "system_error",
                0 if ok else 3,
                envelope.get("summary")
                or f"Batch: {succeeded}/{total} succeeded",
                "",
                self._mode_name(),
                run_id,
                (time.time() - start) * 1000,
                {
                    "total": total,
                    "succeeded": succeeded,
                    "failed": failed,
                    "results": normalized_results,
                },
            )
        except ValueError as exc:
            return failure(str(exc), "system_error", 3)
        except Exception as exc:
            return failure(
                f"Batch request failed: {exc}",
                "system_error",
                3,
            )

    def _print_text(self, result):
        data = result.get("data")
        if isinstance(data, dict):
            text = data.get("text") or result.get("summary", "")
        elif "data" in result:
            text = json.dumps(data, ensure_ascii=False)
        else:
            text = result.get("summary", "")
        text = text.replace("\\n", "\n").replace("\\t", "\t")
        if result.get("ok"):
            print(text) if text else None
        else:
            print(text, file=__import__("sys").stderr)

    def emit(self, result):
        self._output.emit_result(result, as_json=False, print_text=self._print_text)
