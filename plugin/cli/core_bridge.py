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

    def _post(endpoint, payload, timeout=None):
        t = timeout if timeout is not None else default_timeout
        url_base = state.current_server_base_url()
        try:
            return transport_http.post_json(url_base, endpoint, payload, t)
        except OSError:
            # OSError covers ConnectionRefusedError and all socket-level
            # failures, including those raised by third-party HTTP libraries.
            time.sleep(_RETRY_DELAY_S)
            return transport_http.post_json(url_base, endpoint, payload, t)

    return _post


def _coerce_args_json(cmd):
    """Extract argsJson string from a batch command item."""
    args = cmd.get("args")
    if isinstance(args, dict):
        return json.dumps(args, ensure_ascii=False)
    args_json = cmd.get("argsJson") or args
    if isinstance(args_json, str):
        return args_json
    return "{}"


class ConsoleSession:
    """Pre-wired facade over csharpconsole_core. One-liner per command."""

    def __init__(self, project_root, ip="127.0.0.1", port=DEFAULT_EDITOR_PORT, mode="editor", timeout=30,
                 agent_root=None, pkg_dir=None,
                 compile_ip=None, compile_port=None):
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

        self._session_id = client_base.generate_session_id(None)
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

    def command(self, namespace, action, args=None):
        return self._cmd.request_command(
            self._post, self._parser.parse_command_http_response,
            self._mode_name, namespace, action, self._session_id, args,
        )

    def health(self):
        return self._client.request_health(
            self._post, self._parser.parse_health_http_response, self._mode_name,
        )

    def complete(self, code, cursor):
        return self._client.request_completion(
            self._post, self._parser.parse_completion_http_response,
            self._mode_name, self._define, self._using,
            self._state.runtime_mode, self._state.runtime_dll_path,
            code, cursor, self._session_id,
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

    def list_commands(self):
        return self.command("command", "list")

    def batch(self, commands_json, stop_on_error=False):
        """Execute multiple commands in one HTTP roundtrip via /batch endpoint."""
        from csharpconsole_core.models import make_result, new_run_id
        start = time.time()
        run_id = new_run_id()

        if isinstance(commands_json, str):
            try:
                commands = json.loads(commands_json)
            except json.JSONDecodeError as e:
                return make_result(
                    False, "command", "validation_error", 1,
                    f"Invalid JSON: {e}", "", self._mode_name(), run_id, 0,
                )
        else:
            commands = commands_json

        if not isinstance(commands, list):
            return make_result(
                False, "command", "validation_error", 1,
                "Expected a JSON array of commands", "",
                self._mode_name(), run_id, 0,
            )

        items = []
        for cmd in commands:
            if not isinstance(cmd, dict):
                return make_result(
                    False, "command", "validation_error", 1,
                    "Each command must be a JSON object", "",
                    self._mode_name(), run_id, 0,
                )
            items.append({
                "commandNamespace": cmd.get("ns") or cmd.get("commandNamespace") or "",
                "action": cmd.get("action") or "",
                "sessionId": cmd.get("sessionId") or self._session_id,
                "argsJson": _coerce_args_json(cmd),
            })

        payload = {"commands": items, "stopOnError": stop_on_error}
        try:
            raw = self._post("batch", payload)
            # Parse the batch envelope using the same logic as other endpoints:
            # raw is JSON text → parse envelope → extract dataJson
            envelope = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(envelope, dict) or "dataJson" not in envelope:
                return make_result(
                    False, "command", "system_error", 3,
                    "Invalid batch response", "", self._mode_name(), run_id,
                    (time.time() - start) * 1000,
                )

            data_raw = envelope.get("dataJson", "{}")
            data = json.loads(data_raw) if isinstance(data_raw, str) else data_raw
            if not isinstance(data, dict):
                data = {}

            results_raw = data.get("resultsJson", "[]")
            if isinstance(results_raw, str):
                try:
                    results_list = json.loads(results_raw)
                except json.JSONDecodeError:
                    results_list = []
            else:
                results_list = results_raw

            ok = bool(envelope.get("ok"))
            return make_result(
                ok, "command", "" if ok else "system_error",
                0 if ok else 3,
                envelope.get("summary") or f"Batch: {data.get('succeeded', 0)}/{data.get('total', 0)} succeeded",
                "", self._mode_name(), run_id, (time.time() - start) * 1000,
                {
                    "total": data.get("total", 0),
                    "succeeded": data.get("succeeded", 0),
                    "failed": data.get("failed", 0),
                    "results": results_list,
                },
            )
        except Exception as e:
            return make_result(
                False, "command", "system_error", 3,
                f"Batch request failed: {e}", "", self._mode_name(), run_id,
                (time.time() - start) * 1000,
            )

    def _print_text(self, result):
        text = result.get("data", {}).get("text") or result.get("summary", "")
        text = text.replace("\\n", "\n").replace("\\t", "\t")
        if result.get("ok"):
            print(text) if text else None
        else:
            print(text, file=__import__("sys").stderr)

    def emit(self, result):
        self._output.emit_result(result, as_json=False, print_text=self._print_text)
