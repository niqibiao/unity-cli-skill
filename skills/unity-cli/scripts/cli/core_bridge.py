"""Dynamic bridge to csharpconsole_core from an installed Unity package."""

import json
import os
import sys
import time
from pathlib import Path

from cli import PACKAGE_NAME, DEFAULT_EDITOR_PORT, load_pkg_path, save_pkg_path

CORE_RELATIVE = Path("Editor/ExternalTool~/console-client")
_RETRY_DELAY_S = 1


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
    """Create a POST function that retries once when the server is unreachable."""
    # The urllib-based core raises TransportError for every transport failure
    # (connection refused, timeout, non-2xx). Older requests-based cores raised
    # OSError subclasses instead. Catch both so the domain-reload retry survives
    # whichever core version is resolved; fall back to OSError only against a
    # core that predates TransportError.
    transport_error = getattr(transport_http, "TransportError", None)
    transient = (OSError, transport_error) if transport_error is not None else (OSError,)

    def _post(endpoint, payload, timeout=None):
        t = timeout if timeout is not None else default_timeout
        url_base = state.current_server_base_url()
        try:
            return transport_http.post_json(url_base, endpoint, payload, t)
        except transient:
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


def _decode_result_json(value):
    if not isinstance(value, str):
        return value
    if not value.strip():
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _normalize_command_result(result, command_id):
    """Attach the canonical id and expose the existing parser's business data."""
    if not isinstance(result, dict):
        return result
    normalized = dict(result)
    normalized["id"] = command_id
    data = normalized.get("data")
    if isinstance(data, dict) and "resultJson" in data:
        normalized["data"] = data["resultJson"]
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
        return self._cmd.request_command(
            self._post, self._parser.parse_command_http_response,
            self._mode_name, namespace, action, self._session_id, args,
            timeout_seconds=self._timeout,
        )

    def command(self, prepared):
        """Execute one preflighted canonical command."""
        command_id, namespace, action, args = _prepared_command_parts(prepared)
        result = self._request_command(namespace, action, args)
        return _normalize_command_result(result, command_id)

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
            envelope = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(envelope, dict) or "dataJson" not in envelope:
                return failure("Invalid batch response", "system_error", 3)
            data_raw = envelope.get("dataJson", "{}")
            data = (
                json.loads(data_raw)
                if isinstance(data_raw, str)
                else data_raw
            )
            if not isinstance(data, dict):
                data = {}
            results_raw = data.get("resultsJson", "[]")
            results_list = (
                json.loads(results_raw)
                if isinstance(results_raw, str)
                else results_raw
            )
            if not isinstance(results_list, list):
                results_list = []
            normalized_results = []
            for index, item in enumerate(results_list):
                if not isinstance(item, dict):
                    normalized_results.append(
                        {"index": index, "data": item}
                    )
                    continue
                normalized = {
                    "index": index,
                    "ok": item.get("ok"),
                    "type": item.get("type") or "",
                    "summary": item.get("summary") or "",
                    "sessionId": item.get("sessionId") or "",
                    "data": _decode_result_json(
                        item.get("resultJson", "")
                    ),
                }
                if index < len(canonical):
                    command_id, namespace, action = canonical[index]
                    if (
                        item.get("commandNamespace") == namespace
                        and item.get("action") == action
                    ):
                        normalized["id"] = command_id
                normalized_results.append(normalized)

            ok = bool(envelope.get("ok"))
            total = data.get("total", len(normalized_results))
            succeeded = data.get(
                "succeeded",
                sum(1 for item in normalized_results if item.get("ok")),
            )
            failed = data.get("failed", max(0, total - succeeded))
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
