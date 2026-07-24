"""Dynamic bridge to csharpconsole_core from an installed Unity package."""

import errno
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

from cli import PACKAGE_NAME, DEFAULT_EDITOR_PORT, load_pkg_path, save_pkg_path

CORE_RELATIVE = Path("Editor/ExternalTool~/console-client")
_RETRY_DELAY_S = 1
_RELIABLE_ENDPOINTS = frozenset({
    "editor",
    "compile",
    "editor-compile",
    "runtime-compile",
    "refresh",
    "command",
    "batch",
    "execute",
})


def _is_connection_refused(error):
    """Return True only when a request clearly failed before connecting.

    A timeout or a reset can happen after Unity has already executed a
    mutating command. Retrying those failures would risk applying the mutation
    twice, so the automatic domain-reload retry is intentionally limited to a
    refused connection.
    """
    pending = [error]
    seen = set()
    refused_codes = {errno.ECONNREFUSED, 10061}  # POSIX and Winsock

    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        if isinstance(current, ConnectionRefusedError):
            return True
        if getattr(current, "errno", None) in refused_codes:
            return True
        if getattr(current, "winerror", None) in refused_codes:
            return True

        for attribute in ("reason", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)

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


def _decode_envelope(raw):
    """Return ``(envelope, data)`` for a service response, or ``({}, {})``."""
    try:
        envelope = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}, {}
    if not isinstance(envelope, dict):
        return {}, {}
    data = envelope.get("dataJson", {})
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            data = {}
    return envelope, data if isinstance(data, dict) else {}


def _error_envelope(result_type, summary, invocation=None):
    envelope = {
        "ok": False,
        "stage": "bootstrap",
        "type": result_type,
        "summary": summary,
        "sessionId": "",
        "dataJson": "{}",
    }
    if invocation:
        envelope["invocation"] = invocation
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


class _ReliablePost:
    """Operation-aware HTTP adapter used by all ConsoleSession calls.

    It performs a health/capability handshake before a protected endpoint,
    writes a machine-local outbox record before network dispatch, and reuses
    the exact bytes + invocation id for one bounded retry.  The Unity service
    remains the authority for deduplication and result replay.
    """

    def __init__(
        self,
        transport_http,
        state,
        default_timeout,
        project_root,
        explicit_operation_id=None,
    ):
        self._transport = transport_http
        self._state = state
        self._default_timeout = default_timeout
        self._project_root = Path(project_root).resolve()
        self._explicit_operation_id = explicit_operation_id
        self._explicit_consumed = False
        self._health = None
        self._last_invocation = None
        self._last_transport_unknown = None

        from cli.reliability import InvocationOutbox, expected_editor_target_id
        self._expected_target_id = expected_editor_target_id(self._project_root)
        self._outbox = InvocationOutbox(self._project_root)

        # The urllib-based core raises TransportError for every transport
        # failure. Older cores raised OSError subclasses instead.
        transport_error = getattr(transport_http, "TransportError", None)
        self._transient = (
            (OSError, transport_error)
            if transport_error is not None
            else (OSError,)
        )

    @property
    def last_invocation(self):
        return self._last_invocation

    def _next_invocation_id(self):
        if self._explicit_operation_id and not self._explicit_consumed:
            self._explicit_consumed = True
            try:
                return str(uuid.UUID(str(self._explicit_operation_id)))
            except (ValueError, AttributeError, TypeError):
                return None
        return str(uuid.uuid4())

    def _post_json(self, endpoint, payload, timeout, *, headers=None, body=None):
        """Call the protocol-v2 transport without re-serializing *body*."""
        return self._transport.post_json(
            self._state.current_server_base_url(),
            endpoint,
            payload,
            timeout,
            headers=headers,
            body=body,
        )

    def _probe_reliability(self, timeout):
        if self._health is not None:
            return self._health
        raw = None
        for attempt in range(2):
            try:
                raw = self._transport.post_json(
                    self._state.current_server_base_url(),
                    "health",
                    {},
                    min(timeout, 2),
                )
                break
            except self._transient:
                if attempt != 0:
                    raise
                time.sleep(_RETRY_DELAY_S)
        envelope, data = _decode_envelope(raw)
        if not envelope.get("ok") or not data:
            raise RuntimeError("Unity health response is invalid")

        from cli.reliability import inspect_reliability_health
        reliability = inspect_reliability_health(
            data,
            self._expected_target_id,
        )
        if not reliability["targetVerified"] or not reliability["targetMatches"]:
            raise RuntimeError(
                "Unity target mismatch: the reachable service belongs to a "
                "different project"
            )
        if not reliability["protocolSupported"]:
            raise RuntimeError(
                "Unity package does not provide protocol-v2 reliable invocations"
            )
        if reliability["missingCapabilities"]:
            raise RuntimeError(
                "Unity package does not provide the required at-most-once "
                "capabilities: "
                + ", ".join(reliability["missingCapabilities"])
            )
        if not reliability["unitySupported"]:
            raise RuntimeError("The connected Editor is not Unity 2022")
        if not reliability["dedupeWindowValid"]:
            raise RuntimeError(
                "Unity package did not advertise a valid invocation dedupe window"
            )
        if not reliability["journalWritable"]:
            raise RuntimeError(
                "Unity invocation journal is not confirmed writable; refusing to execute"
            )
        self._health = data
        return data

    @staticmethod
    def _command_capability_requirements(endpoint, payload):
        """Return ``(command ids, capabilities)`` for a command request."""
        if endpoint not in {"command", "batch"}:
            return [], set()

        commands = []
        if endpoint == "command":
            invocation = (
                payload.get("invocation")
                if isinstance(payload, dict)
                else None
            )
            command = (
                invocation.get("command")
                if isinstance(invocation, dict)
                else None
            )
            if isinstance(command, dict):
                commands.append(command)
        elif endpoint == "batch" and isinstance(payload, dict):
            batch_commands = payload.get("commands")
            if isinstance(batch_commands, list):
                commands.extend(
                    command
                    for command in batch_commands
                    if isinstance(command, dict)
                )

        from cli.command_index import (
            command_contracts,
            required_command_capabilities,
        )

        contracts = command_contracts()
        command_ids = []
        capabilities = set()
        for command in commands:
            namespace = command.get("commandNamespace") or command.get("ns")
            action = command.get("action")
            if not isinstance(namespace, str) or not isinstance(action, str):
                continue
            required = required_command_capabilities(
                namespace,
                action,
                contracts=contracts,
            )
            if required:
                command_ids.append(f"{namespace}/{action}")
                capabilities.update(required)
        return sorted(set(command_ids)), capabilities

    def _encode_body(self, payload):
        encoder = getattr(self._transport, "encode_json_body", None)
        if encoder is None:
            raise RuntimeError(
                "Installed Unity package core does not support reliable request bytes"
            )
        return encoder(payload)

    @staticmethod
    def _matching_receipt(
        receipt,
        *,
        invocation_id,
        target_id,
        endpoint,
        body,
    ):
        if not isinstance(receipt, dict):
            return None
        try:
            receipt_id = str(uuid.UUID(str(receipt.get("invocationId"))))
        except (ValueError, AttributeError, TypeError):
            return None
        expected_digest = hashlib.sha256(body).hexdigest()
        if (
            receipt_id != invocation_id
            or receipt.get("targetId") != target_id
            or receipt.get("guarantee") != "at-most-once"
            or not receipt.get("state")
        ):
            return None
        if receipt.get("state") == "conflict":
            # A conflict receipt describes the original binding already stored
            # under this id, not the rejected request's endpoint/body. Matching
            # id + target is enough to prove this new request was not executed.
            return dict(receipt)
        if (
            receipt.get("endpoint") != endpoint
            or receipt.get("requestDigest") != expected_digest
        ):
            return None
        return dict(receipt)

    def _record_response(self, invocation_id, target_id, endpoint, body, raw):
        envelope, _ = _decode_envelope(raw)
        receipt = self._matching_receipt(
            envelope.get("invocation"),
            invocation_id=invocation_id,
            target_id=target_id,
            endpoint=endpoint,
            body=body,
        )
        if receipt is None:
            request_digest = hashlib.sha256(body).hexdigest()
            receipt = {
                "invocationId": invocation_id,
                "targetId": target_id,
                "endpoint": endpoint,
                "requestDigest": request_digest,
                "state": "outcome_unknown",
                "guarantee": "unverified",
                "replayed": False,
            }
            self._last_invocation = receipt
            self._last_transport_unknown = (
                "Unity returned no matching durable invocation receipt; the "
                "operation may have executed and was not repeated. "
                f"Inspect it with `cs doctor --operation {invocation_id} --json`."
            )
            try:
                self._outbox.mark_unknown(
                    invocation_id,
                    "missing or inconsistent durable invocation receipt",
                )
            except OSError:
                pass
            return
        self._last_invocation = receipt
        try:
            self._outbox.mark_received(invocation_id, receipt)
        except OSError:
            # The authoritative receipt is already durable on the Unity side.
            # Do not turn a known server result into an unknown result merely
            # because the local audit update failed after the response arrived.
            receipt = dict(receipt)
            receipt["localAuditWarning"] = "failed to update local invocation outbox"
            self._last_invocation = receipt

    def __call__(self, endpoint, payload, timeout=None):
        self._last_invocation = None
        self._last_transport_unknown = None
        request_timeout = timeout if timeout is not None else self._default_timeout
        if endpoint not in _RELIABLE_ENDPOINTS:
            url_base = self._state.current_server_base_url()
            try:
                return self._transport.post_json(
                    url_base, endpoint, payload, request_timeout,
                )
            except self._transient as error:
                if not _is_connection_refused(error):
                    raise
                time.sleep(_RETRY_DELAY_S)
                return self._transport.post_json(
                    url_base, endpoint, payload, request_timeout,
                )

        try:
            health = self._probe_reliability(request_timeout)
        except self._transient as error:
            return _error_envelope(
                "system_error",
                f"Unity reliability preflight could not reach the service: {error}",
            )
        except Exception as error:
            return _error_envelope("capability_missing", str(error))

        command_ids, required_capabilities = self._command_capability_requirements(
            endpoint,
            payload,
        )
        missing_command_capabilities = sorted(
            required_capabilities - set(health.get("capabilities") or [])
        )
        if missing_command_capabilities:
            return _error_envelope(
                "capability_missing",
                "Unity package cannot execute "
                + ", ".join(command_ids)
                + "; missing command capability: "
                + ", ".join(missing_command_capabilities)
                + ". Update the package and verify the live command registry.",
            )

        invocation_id = self._next_invocation_id()
        if not invocation_id:
            return _error_envelope(
                "validation_error",
                "--operation-id must be a UUID",
            )
        if self._explicit_operation_id:
            existing = self._outbox.load(invocation_id)
            if existing is None:
                return _error_envelope(
                    "validation_error",
                    "Explicit operation ids are recovery-only and must already "
                    "exist in the local invocation outbox. Omit --operation-id "
                    "for a new intent.",
                    {
                        "invocationId": invocation_id,
                        "state": "not_executed",
                        "guarantee": "local-no-dispatch",
                        "replayed": False,
                    },
                )

        try:
            body = self._encode_body(payload)
        except Exception as error:
            return _error_envelope("capability_missing", str(error))

        target_id = health.get("targetId") or self._expected_target_id
        request_hash = hashlib.sha256(
            endpoint.encode("utf-8") + b"\n" + body
        ).hexdigest()
        request_digest = hashlib.sha256(body).hexdigest()
        headers = {
            "X-CSharpConsole-Invocation-Id": invocation_id,
            "X-CSharpConsole-Target-Id": target_id,
        }
        try:
            self._outbox.prepare(
                invocation_id,
                target_id=target_id,
                endpoint=endpoint,
                request_hash=request_hash,
                request_digest=request_digest,
            )
            self._outbox.mark_sending(invocation_id)
        except OSError as error:
            error_text = str(error)
            conflict = "already bound to a different request" in error_text
            already_sent = "has already been sent" in error_text
            if already_sent:
                existing = self._outbox.load(invocation_id) or {}
                existing_state = existing.get("state") or "unknown"
                completed = existing_state in {
                    "completed",
                    "succeeded",
                    "failed",
                    "replayed",
                }
                receipt = {
                    "invocationId": invocation_id,
                    "state": existing_state,
                    "guarantee": "local-no-redispatch",
                    "replayed": False,
                }
                self._last_invocation = receipt
                return _error_envelope(
                    (
                        "operation_already_completed"
                        if completed
                        else "outcome_unknown"
                    ),
                    (
                        f"Operation {invocation_id} is already {existing_state}; "
                        "it was not dispatched again. Use a new id only for a "
                        "new intent."
                        if completed
                        else
                        f"Operation {invocation_id} was already sent and was not "
                        "dispatched again. Inspect it with "
                        f"`cs doctor --operation {invocation_id} --json`."
                    ),
                    receipt,
                )
            return _error_envelope(
                "invocation_conflict" if conflict else "invocation_store_unavailable",
                (
                    f"Operation id conflicts with its local request binding: {error}"
                    if conflict
                    else f"Local invocation outbox is not writable: {error}"
                ),
                {
                    "invocationId": invocation_id,
                    "state": "conflict" if conflict else "not_executed",
                    "guarantee": "at-most-once",
                    "replayed": False,
                },
            )

        first_error = None
        last_error = None
        for attempt in range(2):
            try:
                raw = self._post_json(
                    endpoint,
                    payload,
                    request_timeout,
                    headers=headers,
                    body=body,
                )
                self._record_response(
                    invocation_id,
                    target_id,
                    endpoint,
                    body,
                    raw,
                )
                return raw
            except self._transient as error:
                first_error = first_error or error
                last_error = error
                if attempt == 0:
                    time.sleep(_RETRY_DELAY_S)
                    continue

        both_refused = (
            _is_connection_refused(first_error)
            and _is_connection_refused(last_error)
        )
        state = "not_executed" if both_refused else "outcome_unknown"
        receipt = {
            "invocationId": invocation_id,
            "state": state,
            "guarantee": "at-most-once",
            "replayed": False,
        }
        self._last_invocation = receipt
        if state == "outcome_unknown":
            self._last_transport_unknown = (
                "Unity may have applied this operation; it was not repeated. "
                f"Inspect it with `cs doctor --operation {invocation_id} --json`."
            )
        try:
            if state == "outcome_unknown":
                self._outbox.mark_unknown(invocation_id, str(last_error))
            else:
                self._outbox.mark_not_executed(invocation_id, str(last_error))
        except OSError:
            pass
        return _error_envelope(
            "outcome_unknown" if state == "outcome_unknown" else "system_error",
            self._last_transport_unknown
            or f"Unity service is unreachable: {last_error}",
            receipt,
        )

    def annotate_result(self, result):
        """Attach the operation receipt and preserve unknown-outcome semantics."""
        if not isinstance(result, dict) or not self._last_invocation:
            return result
        result = dict(result)
        result["invocation"] = dict(self._last_invocation)
        if self._last_transport_unknown:
            result.update({
                "ok": False,
                "type": "outcome_unknown",
                "exitCode": 4,
                "summary": self._last_transport_unknown,
            })
        elif result.get("type") in {"outcome_unknown", "operation_in_progress"}:
            invocation_id = (
                self._last_invocation.get("invocationId")
                or self._last_invocation.get("id")
            )
            if invocation_id and invocation_id not in (result.get("summary") or ""):
                result["summary"] = (
                    f"{result.get('summary') or 'Operation is unresolved'} "
                    f"Inspect the same id with `cs doctor --operation "
                    f"{invocation_id} --json`; do not replace it with a new id."
                )
        return result


def _make_post_with_retry(
    transport_http,
    state,
    default_timeout,
    project_root=None,
    operation_id=None,
):
    """Create the operation-aware POST adapter for a ConsoleSession."""
    if project_root is None:
        # Compatibility seam for older in-process callers. Product
        # ConsoleSession construction always supplies a project root.
        transport_error = getattr(transport_http, "TransportError", None)
        transient = (
            (OSError, transport_error)
            if transport_error is not None
            else (OSError,)
        )

        def _legacy_post(endpoint, payload, timeout=None):
            request_timeout = (
                timeout if timeout is not None else default_timeout
            )
            url_base = state.current_server_base_url()
            try:
                return transport_http.post_json(
                    url_base, endpoint, payload, request_timeout,
                )
            except transient as error:
                if not _is_connection_refused(error):
                    raise
                time.sleep(_RETRY_DELAY_S)
                return transport_http.post_json(
                    url_base, endpoint, payload, request_timeout,
                )

        return _legacy_post
    return _ReliablePost(
        transport_http,
        state,
        default_timeout,
        project_root,
        explicit_operation_id=operation_id,
    )


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
                 compile_ip=None, compile_port=None, session_id=None,
                 operation_id=None):
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
        self._post = _make_post_with_retry(
            transport_http,
            state,
            timeout,
            project_root,
            operation_id=operation_id,
        )
        self._mode_name = lambda: state.current_mode_name()
        # Placeholders required by csharpconsole_core API for persistent
        # using/define directives. Empty for CLI usage; the interactive REPL
        # populates these from DefaultUsing.cs / Defines.txt files.
        self._define = lambda: ""
        self._using = lambda: ""

    def _annotate_result(self, result):
        annotate = getattr(self._post, "annotate_result", None)
        return annotate(result) if annotate else result

    def exec(self, code, reset=False):
        # In runtime mode, the snippet must be compiled by the editor and
        # forwarded to the player — execute_runtime_request POSTs to the
        # "compile" endpoint with targetIP/targetPort. Without this branch
        # we'd POST to "editor" and silently run in the local editor.
        if self._state.runtime_mode:
            result = self._client.execute_runtime_request(
                self._post, self._parser.parse_text_http_response,
                self._define, self._using,
                self._state.runtime_ip, self._state.runtime_port,
                self._state.runtime_dll_path,
                code, self._session_id, reset,
            )
        else:
            result = self._client.execute_editor_request(
                self._post, self._parser.parse_text_http_response,
                self._define, self._using, code, self._session_id, reset,
            )
        return self._annotate_result(result)

    def command(self, namespace, action, args=None):
        result = self._cmd.request_command(
            self._post, self._parser.parse_command_http_response,
            self._mode_name, namespace, action, self._session_id, args,
            timeout_seconds=self._timeout,
        )
        return self._annotate_result(result)

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
            result = self._client.request_refresh(
                self._post, self._parser.parse_refresh_http_response, self._mode_name,
            )
            return self._annotate_result(result)

        from csharpconsole_core.models import make_result, new_run_id
        start = time.time()
        run_id = new_run_id()
        try:
            raw = self._post("refresh", payload)
            result = self._parser.parse_refresh_http_response(
                raw, self._mode_name(), run_id, (time.time() - start) * 1000,
            )
            return self._annotate_result(result)
        except Exception as e:
            result = make_result(
                False, "bootstrap", "system_error", 3,
                f"Refresh request failed: {e}", "",
                self._mode_name(), run_id, (time.time() - start) * 1000,
            )
            return self._annotate_result(result)

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
            result = make_result(
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
            result_type = envelope.get("type") or result.get("type")
            if not ok:
                result["type"] = result_type
                if result_type in {"outcome_unknown", "operation_in_progress"}:
                    result["exitCode"] = 4
            if isinstance(envelope.get("invocation"), dict):
                result["invocation"] = envelope["invocation"]
            return self._annotate_result(result)
        except Exception as e:
            result = make_result(
                False, "command", "system_error", 3,
                f"Batch request failed: {e}", "", self._mode_name(), run_id,
                (time.time() - start) * 1000,
            )
            return self._annotate_result(result)

    def invocation_status(self, invocation_id):
        """Inspect one server-side invocation without creating a new operation."""
        from csharpconsole_core.models import make_result, new_run_id
        start = time.time()
        run_id = new_run_id()
        payload = {
            "invocationId": invocation_id,
            "targetId": self._post._expected_target_id,
        }
        try:
            raw = self._post("invocation-status", payload, min(self._timeout, 5))
            envelope, data = _decode_envelope(raw)
            ok = bool(envelope.get("ok"))
            result_type = envelope.get("type") or ("" if ok else "system_error")
            return make_result(
                ok,
                "bootstrap",
                result_type,
                0 if ok else (
                    4
                    if result_type in {"outcome_unknown", "operation_in_progress"}
                    else 3
                ),
                envelope.get("summary") or "Invocation status",
                "",
                self._mode_name(),
                run_id,
                (time.time() - start) * 1000,
                data,
            )
        except Exception as error:
            return make_result(
                False,
                "bootstrap",
                "system_error",
                3,
                f"Invocation status failed: {error}",
                "",
                self._mode_name(),
                run_id,
                (time.time() - start) * 1000,
            )

    def _print_text(self, result):
        if result.get("type") in {"outcome_unknown", "operation_in_progress"}:
            # annotate_result puts the stable invocation id and recovery
            # command in summary. Never let a server-provided text payload hide
            # that information in non-JSON output.
            text = (
                result.get("summary", "")
                or result.get("data", {}).get("text")
                or ""
            )
        else:
            text = result.get("data", {}).get("text") or result.get("summary", "")
        text = text.replace("\\n", "\n").replace("\\t", "\t")
        if result.get("ok"):
            print(text) if text else None
        else:
            print(text, file=__import__("sys").stderr)

    def emit(self, result):
        self._output.emit_result(result, as_json=False, print_text=self._print_text)
