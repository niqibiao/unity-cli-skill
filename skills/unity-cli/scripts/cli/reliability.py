"""Reliability coordination for Unity service discovery and invocations.

This module owns three concerns that otherwise leak into every CLI handler:

* stable project identity and ready-state reduction;
* Unity/project-read-only doctor and wait-ready diagnostics, including a
  machine-local outbox writability probe;
* a strict machine-local invocation outbox.

The Unity package remains authoritative for execution and deduplication.  The
outbox is an audit/recovery aid and never substitutes for the server journal.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cli import PACKAGE_NAME, DEFAULT_EDITOR_PORT
from cli.paths import state_dir


PROTOCOL_VERSION = 2
REQUIRED_CAPABILITIES = frozenset({
    "at_most_once",
    "invocation_headers",
    "invocation_receipts",
    "invocation_status",
})
EDITOR_PORT_SCAN_COUNT = 10
DEFAULT_POLL_INTERVAL = 0.5
HEARTBEAT_STALE_MS = 5000


def inspect_reliability_health(health, expected_target_id=None):
    """Reduce one health payload to the shared reliability policy facts."""
    health = health if isinstance(health, dict) else {}
    target_id = health.get("targetId") or ""
    unity_version = health.get("unityVersion")
    protocol = health.get("protocolVersion")
    capabilities = set(health.get("capabilities") or [])
    dedupe_window = health.get("dedupeWindowSeconds")
    return {
        "targetId": target_id,
        "targetVerified": bool(target_id),
        "targetMatches": (
            not expected_target_id or target_id == expected_target_id
        ),
        "unitySupported": (
            isinstance(unity_version, str)
            and unity_version.startswith("2022.")
        ),
        "protocolVersion": protocol,
        "protocolSupported": (
            isinstance(protocol, int)
            and not isinstance(protocol, bool)
            and protocol >= PROTOCOL_VERSION
        ),
        "missingCapabilities": sorted(
            REQUIRED_CAPABILITIES - capabilities
        ),
        "dedupeWindowSeconds": dedupe_window,
        "dedupeWindowValid": (
            isinstance(dedupe_window, int)
            and not isinstance(dedupe_window, bool)
            and dedupe_window > 0
        ),
        "journalWritable": health.get(
            "invocationJournalWritable",
            health.get("journalWritable"),
        ) is True,
    }


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_project_root(project_root):
    path = str(Path(project_root).resolve()).rstrip("\\/")
    if os.name == "nt":
        path = path.lower()
    return path.replace("\\", "/")


def expected_editor_target_id(project_root):
    """Return the protocol-v2 target id for an Editor project.

    Prefer the package's project-local identity once Unity has initialized it.
    This keeps junction/symlink launches aligned without trusting a network
    response. Fall back to the package-side path algorithm before first launch.
    The raw path is never sent or persisted in the service health response.
    """
    identity_path = (
        Path(project_root)
        / "Library"
        / "CSharpConsole"
        / "InvocationLedger"
        / "v1"
        / "identity.json"
    )
    try:
        identity = json.loads(identity_path.read_text("utf-8"))
        persisted = identity.get("targetId")
        persisted_root = identity.get("projectRoot")
    except (OSError, ValueError, AttributeError):
        persisted = None
        persisted_root = None
    persisted_root_matches = False
    if isinstance(persisted_root, str) and persisted_root:
        try:
            persisted_root_matches = (
                _normalize_project_root(persisted_root)
                == _normalize_project_root(project_root)
            )
        except OSError:
            persisted_root_matches = False
    if (
        persisted_root_matches
        and isinstance(persisted, str)
        and persisted.startswith("editor-")
        and len(persisted) == len("editor-") + 24
        and all(
            character in "0123456789abcdef"
            for character in persisted[len("editor-"):]
        )
    ):
        return persisted

    normalized = _normalize_project_root(project_root)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"editor-{digest[:24]}"


def _strict_atomic_write(path, value):
    """Durably replace a JSON file or raise; never silently lose an outbox write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


class InvocationOutbox:
    """Strict per-project operation audit stored outside the Unity project."""

    def __init__(self, project_root):
        self._root = state_dir(project_root) / "invocations" / "v1"
        self._lock_path = self._root / ".lock"

    @contextmanager
    def _exclusive_lock(self):
        self._root.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _normalize_id(invocation_id):
        try:
            return str(uuid.UUID(str(invocation_id)))
        except (ValueError, AttributeError, TypeError) as error:
            raise OSError(f"invalid invocation id: {invocation_id}") from error

    def _path(self, invocation_id):
        normalized = self._normalize_id(invocation_id)
        return self._root / f"{normalized}.json"

    def load(self, invocation_id):
        path = self._path(invocation_id)
        try:
            value = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            return {
                "id": self._normalize_id(invocation_id),
                "state": "local_record_unreadable",
                "error": str(error),
            }
        if not isinstance(value, dict):
            return {
                "id": self._normalize_id(invocation_id),
                "state": "local_record_unreadable",
                "error": "local invocation record is not a JSON object",
            }
        return value

    def probe_writable(self):
        """Return ``None`` when a strict outbox write/delete probe succeeds."""
        probe = self._root / f".doctor-probe.{os.getpid()}.{uuid.uuid4().hex}"
        try:
            with self._exclusive_lock():
                with probe.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write("ok\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                probe.unlink()
            return None
        except OSError as error:
            try:
                probe.unlink()
            except OSError:
                pass
            return str(error)

    def _update(self, invocation_id, state, **fields):
        with self._exclusive_lock():
            record = self.load(invocation_id) or {
                "id": self._normalize_id(invocation_id),
                "createdAtUtc": _utc_now(),
            }
            record.update(fields)
            record["state"] = state
            record["updatedAtUtc"] = _utc_now()
            _strict_atomic_write(self._path(invocation_id), record)
            return record

    def prepare(
        self,
        invocation_id,
        *,
        target_id,
        endpoint,
        request_hash,
        request_digest=None,
    ):
        normalized = self._normalize_id(invocation_id)
        with self._exclusive_lock():
            existing = self.load(normalized)
            if existing:
                expected = (
                    existing.get("targetId"),
                    existing.get("endpoint"),
                    existing.get("requestHash"),
                )
                actual = (target_id, endpoint, request_hash)
                if expected != actual:
                    raise OSError(
                        "operation id is already bound to a different request"
                    )
                existing_digest = existing.get("requestDigest")
                if (
                    request_digest is not None
                    and existing_digest not in (None, request_digest)
                ):
                    raise OSError(
                        "operation id is already bound to a different request"
                    )
                if request_digest is not None and existing_digest is None:
                    existing["requestDigest"] = request_digest
                    existing["updatedAtUtc"] = _utc_now()
                    _strict_atomic_write(self._path(normalized), existing)
                if existing.get("state") not in {
                    "prepared",
                    "not_executed",
                    "rejected",
                }:
                    raise OSError(
                        "operation id has already been sent "
                        f"(state={existing.get('state') or 'unknown'}); "
                        "inspect it with doctor instead of dispatching it again"
                    )
                return existing
            record = {
                "id": normalized,
                "state": "prepared",
                "targetId": target_id,
                "endpoint": endpoint,
                "requestHash": request_hash,
                "requestDigest": request_digest,
                "createdAtUtc": _utc_now(),
                "updatedAtUtc": _utc_now(),
            }
            _strict_atomic_write(self._path(normalized), record)
            return record

    def mark_sending(self, invocation_id):
        return self._update(invocation_id, "sending", sentAtUtc=_utc_now())

    def mark_received(self, invocation_id, receipt):
        return self._update(
            invocation_id,
            (receipt or {}).get("state") or "completed",
            receivedAtUtc=_utc_now(),
            receipt=receipt or {},
        )

    def mark_unknown(self, invocation_id, error):
        return self._update(
            invocation_id,
            "outcome_unknown",
            lastError=error,
            doNotRetryAsNewOperation=True,
        )

    def mark_not_executed(self, invocation_id, error):
        return self._update(
            invocation_id,
            "not_executed",
            lastError=error,
        )


def _json_body(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_envelope(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("response is not a JSON object")
    data = envelope.get("dataJson", {})
    if isinstance(data, str):
        data = json.loads(data or "{}")
    if not isinstance(data, dict):
        data = {}
    return envelope, data


class HttpConsoleAdapter:
    """Small remote-owned seam for the Unity HTTP service."""

    def __init__(self, ip):
        self.ip = ip

    def post(self, port, endpoint, payload, timeout=2, headers=None):
        url = f"http://{self.ip}:{port}/CSharpConsole/{endpoint}"
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        request = urllib.request.Request(
            url,
            data=_json_body(payload),
            headers=request_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset)
        except urllib.error.HTTPError as error:
            # Preserve a structured body if a future service uses non-2xx.
            body = error.read().decode("utf-8", errors="replace")
            if body:
                return body
            raise OSError(f"HTTP {error.code} {error.reason}") from error
        except OSError:
            raise


def _finding(code, severity, summary, remediation=None, evidence=None):
    item = {
        "code": code,
        "severity": severity,
        "summary": summary,
    }
    if remediation:
        item["remediation"] = remediation
    if evidence is not None:
        item["evidence"] = evidence
    return item


def _read_package_version(package_dir):
    if not package_dir:
        return None
    try:
        data = json.loads((Path(package_dir) / "package.json").read_text("utf-8"))
        return data.get("version")
    except (OSError, ValueError, AttributeError):
        return None


def _parse_major_minor(version):
    if not isinstance(version, str):
        return None
    parts = version.lstrip("v").split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _find_package(project_root):
    if not project_root:
        return None
    root = Path(project_root)
    manifest = root / "Packages" / "manifest.json"
    try:
        dependencies = json.loads(manifest.read_text("utf-8")).get(
            "dependencies", {}
        )
    except (OSError, ValueError, AttributeError):
        dependencies = {}
    source = dependencies.get(PACKAGE_NAME, "")
    if isinstance(source, str) and source.startswith("file:"):
        path = (root / "Packages" / source[len("file:"):]).resolve()
        if (path / "package.json").is_file():
            return path

    cache = root / "Library" / "PackageCache"
    try:
        candidates = sorted(cache.iterdir())
    except OSError:
        candidates = []
    for candidate in candidates:
        if (
            candidate.name == PACKAGE_NAME
            or candidate.name.startswith(PACKAGE_NAME + "@")
        ) and (candidate / "package.json").is_file():
            return candidate
    return None


def _read_plugin_version():
    try:
        return (Path(__file__).with_name("VERSION")).read_text("utf-8").strip()
    except OSError:
        return None


def _health_is_ready(data):
    operation = data.get("operation") or {}
    phase = operation.get("phase") or ""
    heartbeat_age = data.get("mainThreadHeartbeatAgeMs")
    heartbeat_ready = (
        isinstance(heartbeat_age, (int, float))
        and not isinstance(heartbeat_age, bool)
        and 0 <= heartbeat_age <= HEARTBEAT_STALE_MS
    )
    return (
        bool(data.get("initialized"))
        and data.get("editorState") == "ready"
        and not data.get("refreshing")
        and not data.get("isCompiling")
        and not data.get("isUpdating")
        and not data.get("compileFailed")
        and phase in ("", "ready")
        and heartbeat_ready
    )


class ReliabilityCoordinator:
    """Deep coordinator behind doctor, wait-ready, and invocation recovery."""

    def __init__(
        self,
        project_root,
        *,
        ip="127.0.0.1",
        port=DEFAULT_EDITOR_PORT,
        mode="editor",
        compile_ip=None,
        compile_port=None,
        adapter=None,
        clock=None,
        sleeper=None,
    ):
        self.project_root = (
            Path(project_root).resolve() if project_root is not None else None
        )
        self.ip = (
            compile_ip or "127.0.0.1"
            if mode == "runtime"
            else ip
        )
        self.port = (
            compile_port or DEFAULT_EDITOR_PORT
            if mode == "runtime"
            else port
        )
        self.mode = mode
        self.adapter = adapter or HttpConsoleAdapter(self.ip)
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self.expected_target = (
            expected_editor_target_id(self.project_root)
            if self.project_root is not None
            else None
        )
        self.outbox = (
            InvocationOutbox(self.project_root)
            if self.project_root is not None
            else None
        )
        self._outbox_probe_checked = False
        self._outbox_probe_error = None

    def _refresh_expected_target(self):
        if self.project_root is None:
            return
        self.expected_target = expected_editor_target_id(self.project_root)

    def _candidate_ports(self):
        values = [int(self.port or DEFAULT_EDITOR_PORT)]
        if self.ip in {"127.0.0.1", "localhost", "::1"}:
            values.extend(
                range(DEFAULT_EDITOR_PORT, DEFAULT_EDITOR_PORT + EDITOR_PORT_SCAN_COUNT)
            )
        return list(dict.fromkeys(values))

    def _probe(
        self,
        *,
        deadline=None,
        expected_operation_id=None,
        minimum_generation=None,
    ):
        # Unity creates the project-local identity during service startup. A
        # long-running wait may begin before that file exists (notably when the
        # project was opened through a junction), so do not freeze the fallback
        # path-derived target for the whole coordinator lifetime.
        self._refresh_expected_target()
        errors = []
        mismatches = []
        unverified = []
        stale_matches = []
        for port in self._candidate_ports():
            request_timeout = 2.0
            if deadline is not None:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    break
                request_timeout = min(request_timeout, max(0.001, remaining))
            try:
                raw = self.adapter.post(
                    port,
                    "health",
                    {},
                    timeout=request_timeout,
                )
                envelope, data = _decode_envelope(raw)
            except Exception as error:
                errors.append({"port": port, "error": str(error)})
                continue
            if not envelope.get("ok"):
                errors.append({
                    "port": port,
                    "error": envelope.get("summary") or "health returned ok=false",
                })
                continue
            target = data.get("targetId")
            if self.expected_target:
                if not target:
                    unverified.append({
                        "reachable": True,
                        "port": port,
                        "envelope": envelope,
                        "health": data,
                    })
                    continue
                if target != self.expected_target:
                    # The identity may have appeared while this port scan was
                    # in flight. Only expected_editor_target_id can adopt it,
                    # and that helper verifies its persisted projectRoot first.
                    self._refresh_expected_target()
                if target != self.expected_target:
                    mismatches.append({"port": port, "targetId": target})
                    continue
            if expected_operation_id or minimum_generation is not None:
                operation = data.get("operation") or {}
                operation_matches = (
                    not expected_operation_id
                    or operation.get("opId") == expected_operation_id
                )
                generation_matches = (
                    minimum_generation is None
                    or int(data.get("generation") or 0)
                    >= int(minimum_generation)
                )
                if not operation_matches or not generation_matches:
                    stale_matches.append({
                        "reachable": True,
                        "port": port,
                        "envelope": envelope,
                        "health": data,
                    })
                    continue
            return {
                "reachable": True,
                "port": port,
                "envelope": envelope,
                "health": data,
                "errors": errors,
                "mismatches": mismatches,
            }
        if stale_matches:
            fallback = dict(stale_matches[0])
            fallback["errors"] = errors
            fallback["mismatches"] = mismatches
            fallback["staleMatches"] = [
                {"port": item["port"]}
                for item in stale_matches
            ]
            return fallback
        if unverified:
            fallback = dict(unverified[0])
            fallback["errors"] = errors
            fallback["mismatches"] = mismatches
            fallback["unverified"] = [
                {"port": item["port"]}
                for item in unverified
            ]
            return fallback
        return {
            "reachable": False,
            "port": None,
            "health": {},
            "errors": errors,
            "mismatches": mismatches,
        }

    def _inspect_operation(
        self,
        invocation_id,
        probe,
        *,
        verbose=False,
        deadline=None,
    ):
        local = self.outbox.load(invocation_id) if self.outbox else None
        local_receipt_trusted = False
        if isinstance(local, dict):
            local_receipt = local.get("receipt")
            if isinstance(local_receipt, dict):
                try:
                    receipt_id = str(uuid.UUID(
                        str(local_receipt.get("invocationId"))
                    ))
                except (ValueError, AttributeError, TypeError):
                    receipt_id = None
                digest = local_receipt.get("requestDigest")
                local_receipt_trusted = (
                    receipt_id == invocation_id
                    and local.get("id") == invocation_id
                    and local_receipt.get("targetId") == local.get("targetId")
                    and local_receipt.get("targetId") == self.expected_target
                    and local_receipt.get("endpoint") == local.get("endpoint")
                    and local_receipt.get("requestDigest")
                    == local.get("requestDigest")
                    and local_receipt.get("guarantee") == "at-most-once"
                    and isinstance(digest, str)
                    and len(digest) == 64
                    and all(character in "0123456789abcdefABCDEF" for character in digest)
                )
        server = None
        server_error = None
        if probe.get("reachable"):
            payload = {
                "invocationId": invocation_id,
                "targetId": self.expected_target or "",
            }
            headers = {}
            if self.expected_target:
                headers["X-CSharpConsole-Target-Id"] = self.expected_target
            try:
                request_timeout = 3.0
                if deadline is not None:
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise TimeoutError("diagnostic timeout expired")
                    request_timeout = min(request_timeout, max(0.001, remaining))
                raw = self.adapter.post(
                    probe["port"],
                    "invocation-status",
                    payload,
                    timeout=request_timeout,
                    headers=headers,
                )
                envelope, data = _decode_envelope(raw)
                if not envelope.get("ok"):
                    server_error = envelope.get("summary")
                elif (
                    data.get("invocationId") != invocation_id
                    or data.get("targetId") != self.expected_target
                ):
                    server_error = (
                        "Invocation status response identity does not match the "
                        "requested operation."
                    )
                elif (
                    (
                        data.get("found")
                        or data.get("protectionExpired")
                        or data.get("state") == "protection_expired"
                    )
                    and isinstance(local, dict)
                    and local.get("state") != "conflict"
                    and not (
                        isinstance(local.get("receipt"), dict)
                        and local["receipt"].get("state") == "conflict"
                    )
                    and (
                        not local.get("endpoint")
                        or not local.get("requestDigest")
                        or data.get("endpoint") != local.get("endpoint")
                        or str(data.get("requestDigest") or "").lower()
                        != str(local.get("requestDigest") or "").lower()
                    )
                ):
                    server_error = (
                        "Invocation status response does not match the local "
                        "endpoint and request-digest binding."
                    )
                else:
                    server = data
            except Exception as error:
                server_error = str(error)
        if not verbose:
            if isinstance(local, dict):
                local_receipt = local.get("receipt")
                local = {
                    key: local.get(key)
                    for key in ("id", "state", "endpoint", "updatedAtUtc")
                    if local.get(key) is not None
                }
                if isinstance(local_receipt, dict):
                    local["receipt"] = {
                        key: local_receipt.get(key)
                        for key in (
                            "invocationId",
                            "state",
                            "guarantee",
                            "replayed",
                        )
                        if local_receipt.get(key) is not None
                    }
            if isinstance(server, dict):
                server = {
                    key: server.get(key)
                    for key in (
                        "found",
                        "invocationId",
                        "targetId",
                        "serviceEpoch",
                        "endpoint",
                        "state",
                        "protectionExpired",
                        "previousState",
                        "createdAtUtc",
                        "updatedAtUtc",
                    )
                    if server.get(key) is not None
                }
        return {
            "id": invocation_id,
            "local": local,
            "localReceiptTrusted": local_receipt_trusted,
            "server": server,
            "serverError": server_error,
        }

    def _report_from_probe(
        self,
        probe,
        *,
        operation_id=None,
        verbose=False,
        deadline=None,
    ):
        findings = []
        package_dir = _find_package(self.project_root)
        plugin_version = _read_plugin_version()
        package_version = _read_package_version(package_dir)

        if self.project_root is None:
            findings.append(_finding(
                "project.not_found",
                "error",
                "No Unity project was found.",
                "Run from a Unity project or pass --project.",
            ))
        else:
            findings.append(_finding(
                "project.found",
                "info",
                "Unity project detected.",
                evidence={"path": str(self.project_root)} if verbose else None,
            ))

        if self.outbox is not None:
            if not self._outbox_probe_checked:
                self._outbox_probe_error = self.outbox.probe_writable()
                self._outbox_probe_checked = True
            if self._outbox_probe_error:
                findings.append(_finding(
                    "outbox.unwritable",
                    "error",
                    "The machine-local invocation outbox is not writable.",
                    "Restore write access to the unity-cli user cache.",
                    (
                        {"error": self._outbox_probe_error}
                        if verbose
                        else None
                    ),
                ))
            else:
                findings.append(_finding(
                    "outbox.writable",
                    "info",
                    "The machine-local invocation outbox is writable.",
                ))

        if package_dir is None:
            findings.append(_finding(
                "package.not_installed",
                "error",
                "The C# Console package is not resolved.",
                "Run `cs setup` only after approving the exact package source.",
                {"requiresApproval": True},
            ))
        else:
            findings.append(_finding(
                "package.installed",
                "info",
                "The C# Console package is resolved.",
                evidence={
                    "path": str(package_dir),
                    "version": package_version,
                } if verbose else {"version": package_version},
            ))
            if (
                _parse_major_minor(plugin_version)
                and _parse_major_minor(package_version)
                and _parse_major_minor(plugin_version)
                != _parse_major_minor(package_version)
            ):
                findings.append(_finding(
                    "version.mismatch",
                    "error",
                    "unity-cli and the package are on different compatibility lines.",
                    "Align their major.minor versions, then re-run `cs setup`.",
                    {
                        "plugin": plugin_version,
                        "package": package_version,
                    },
                ))

        health = probe.get("health") or {}
        if not probe.get("reachable"):
            if probe.get("mismatches"):
                findings.append(_finding(
                    "target.mismatch",
                    "error",
                    "Reachable Unity services belong to other projects.",
                    "Start this project with UnityStart.cmd or pass the correct port.",
                    probe["mismatches"],
                ))
            else:
                findings.append(_finding(
                    "service.unreachable",
                    "error",
                    "The Unity service is not reachable.",
                    "Start Unity 2022 with UnityStart.cmd, then run `cs wait-ready`.",
                    probe.get("errors") if verbose else None,
                ))
        else:
            findings.append(_finding(
                "service.reachable",
                "info",
                "The Unity 2022 service is reachable.",
                evidence={"port": probe["port"]},
            ))
            reliability = inspect_reliability_health(
                health,
                self.expected_target,
            )
            if not reliability["targetVerified"]:
                findings.append(_finding(
                    "target.unverified",
                    "error",
                    "The service cannot prove which Unity project it belongs to.",
                    "Use a package with protocol v2 target identity.",
                ))
            elif not reliability["targetMatches"]:
                findings.append(_finding(
                    "target.mismatch",
                    "error",
                    "The service belongs to a different Unity project.",
                    "Use the port reported by this project's service.",
                ))

            if not reliability["unitySupported"]:
                findings.append(_finding(
                    "unity.unsupported",
                    "error",
                    "The connected Editor is not Unity 2022.",
                    "Open this project with Unity 2022.",
                ))

            if (
                not reliability["protocolSupported"]
                or reliability["missingCapabilities"]
                or not reliability["dedupeWindowValid"]
            ):
                findings.append(_finding(
                    "protocol.reliability_missing",
                    "error",
                    "The installed service does not support reliable invocations.",
                    "Update the C# Console package to the protocol-v2 release.",
                    {
                        "protocolVersion": reliability["protocolVersion"],
                        "missing": reliability["missingCapabilities"],
                        "dedupeWindowSeconds": reliability[
                            "dedupeWindowSeconds"
                        ],
                    },
                ))
            elif not reliability["journalWritable"]:
                findings.append(_finding(
                    "journal.unwritable",
                    "error",
                    "The Unity invocation journal is not writable.",
                    "Restore write access to the project's Library directory.",
                ))
            else:
                findings.append(_finding(
                    "protocol.at_most_once",
                    "info",
                    "At-most-once invocation protection is available.",
                    evidence={
                        "dedupeWindowSeconds": health.get("dedupeWindowSeconds"),
                    },
                ))

            operation = health.get("operation") or {}
            if health.get("compileFailed"):
                findings.append(_finding(
                    "editor.compile_failed",
                    "error",
                    "Unity compilation failed.",
                    "Fix Console compilation errors, then run `cs wait-ready`.",
                ))
            elif operation.get("phase") == "failed":
                findings.append(_finding(
                    "editor.refresh_failed",
                    "error",
                    operation.get("message") or "Unity refresh failed.",
                    "Inspect the failure, then run a new `cs refresh --wait`.",
                ))
            elif _health_is_ready(health):
                findings.append(_finding(
                    "editor.ready",
                    "info",
                    "Unity 2022 is ready.",
                ))
            else:
                state = (
                    operation.get("message")
                    or health.get("editorState")
                    or "not ready"
                )
                findings.append(_finding(
                    "editor.not_ready",
                    "error",
                    f"Unity 2022 is not ready: {state}.",
                    "Run `cs wait-ready` to wait without triggering a mutation.",
                ))

        operation_report = None
        operation_uncertain = False
        operation_result_type = None
        operation_summary = None
        if operation_id:
            try:
                normalized = str(uuid.UUID(str(operation_id)))
            except (ValueError, TypeError, AttributeError):
                normalized = None
                findings.append(_finding(
                    "operation.invalid_id",
                    "error",
                    "--operation must be a UUID.",
                ))
            if normalized:
                operation_report = self._inspect_operation(
                    normalized,
                    probe,
                    verbose=verbose,
                    deadline=deadline,
                )
                server = operation_report.get("server") or {}
                local = operation_report.get("local") or {}
                local_receipt = local.get("receipt") or {}
                state = server.get("state")
                local_state = local.get("state")
                local_receipt_state = (
                    local_receipt.get("state")
                    if isinstance(local_receipt, dict)
                    else None
                )
                local_receipt_trusted = bool(
                    operation_report.get("localReceiptTrusted")
                )
                completed_states = {"completed", "succeeded", "failed", "replayed"}
                unknown_states = {
                    "outcome_unknown",
                    "unknown",
                    "expired_unknown",
                }
                in_progress_states = {"started", "sending", "in_progress"}

                if state in completed_states:
                    severity = "info"
                    summary = f"Operation {normalized} is {state} and will not be repeated."
                elif state in unknown_states:
                    severity = "error"
                    summary = (
                        f"Operation {normalized} may have executed; do not retry it "
                        "with a new id."
                    )
                    operation_uncertain = True
                    operation_result_type = "outcome_unknown"
                elif state in in_progress_states:
                    severity = "warning"
                    summary = f"Operation {normalized} is still in progress."
                    operation_uncertain = True
                    operation_result_type = "operation_in_progress"
                elif state == "protection_expired":
                    previous_state = server.get("previousState")
                    if previous_state == "completed":
                        severity = "warning"
                        summary = (
                            f"Operation {normalized} completed, but its server "
                            "at-most-once protection window has expired. This "
                            "CLI will not dispatch that id again."
                        )
                    else:
                        severity = "error"
                        summary = (
                            f"Operation {normalized} protection expired after "
                            f"state {previous_state or 'unknown'}; its outcome "
                            "must be treated as unresolved and the id must not "
                            "be dispatched again."
                        )
                        operation_uncertain = True
                        operation_result_type = "outcome_unknown"
                elif (
                    state == "not_found"
                    and local_receipt_trusted
                    and local_receipt_state in completed_states
                ):
                    severity = "warning"
                    state = "protection_expired"
                    summary = (
                        f"Operation {normalized} has a local {local_receipt_state} "
                        "receipt, but the server no longer retains its protection "
                        "record. This CLI will not dispatch that id again."
                    )
                elif local_receipt_trusted and local_receipt_state in completed_states:
                    severity = "info"
                    state = local_receipt_state
                    summary = (
                        f"Operation {normalized} has a durable local {state} "
                        "receipt. This CLI will not dispatch that id again."
                    )
                elif local_state in unknown_states or local_state in in_progress_states:
                    severity = "error"
                    state = local_state
                    summary = (
                        f"Operation {normalized} may have been sent, but no durable "
                        "server result is available."
                    )
                    operation_uncertain = True
                    operation_result_type = "outcome_unknown"
                elif local_state in {
                    "prepared",
                    "not_executed",
                    "rejected",
                }:
                    severity = "info"
                    state = local_state
                    summary = f"Operation {normalized} was definitely not executed."
                elif local_state == "conflict":
                    severity = "error"
                    state = local_state
                    summary = (
                        f"Operation id {normalized} belongs to a different request; "
                        "the original operation outcome is not confirmed."
                    )
                    operation_uncertain = True
                    operation_result_type = "outcome_unknown"
                elif operation_report.get("local"):
                    severity = "warning"
                    summary = (
                        f"Operation {normalized} exists locally but has no confirmed "
                        "server result."
                    )
                    operation_uncertain = True
                    operation_result_type = "outcome_unknown"
                else:
                    severity = "warning"
                    summary = f"Operation {normalized} was not found."
                    operation_uncertain = True
                    operation_result_type = "outcome_unknown"
                operation_summary = summary
                findings.append(_finding(
                    f"operation.{state or 'unconfirmed'}",
                    severity,
                    summary,
                    (
                        "Read back the affected Unity state before deciding on a "
                        "new operation."
                        if severity != "info"
                        else None
                    ),
                ))

        errors = [item for item in findings if item["severity"] == "error"]
        ready = (
            bool(probe.get("reachable"))
            and _health_is_ready(health)
            and not errors
            and not operation_uncertain
        )
        data = {
            "ready": ready,
            "unity": "Unity 2022",
            "port": probe.get("port"),
            "targetId": health.get("targetId"),
            "serviceEpoch": health.get("serviceEpoch"),
            "findings": findings,
        }
        if operation_report is not None:
            data["operation"] = operation_report
        if verbose and health:
            data["rawHealth"] = health

        if operation_uncertain:
            summary = operation_summary or (
                "The operation outcome is not confirmed; do not repeat it with a new id."
            )
            result_type = operation_result_type or "outcome_unknown"
            exit_code = 4
        elif ready:
            summary = "Unity 2022 is ready."
            result_type = "ready"
            exit_code = 0
        else:
            summary = errors[0]["summary"] if errors else "Unity 2022 is not ready."
            result_type = "not_ready"
            exit_code = 3
        return {
            "ok": ready,
            "stage": "bootstrap",
            "type": result_type,
            "exitCode": exit_code,
            "summary": summary,
            "data": data,
        }

    def doctor(self, *, operation_id=None, verbose=False, timeout=None):
        deadline = (
            self.clock() + max(0.0, float(timeout))
            if timeout is not None
            else None
        )
        return self._report_from_probe(
            self._probe(deadline=deadline),
            operation_id=operation_id,
            verbose=verbose,
            deadline=deadline,
        )

    def wait_ready(
        self,
        timeout,
        *,
        expected_operation_id=None,
        minimum_generation=None,
        verbose=False,
        poll_interval=DEFAULT_POLL_INTERVAL,
    ):
        timeout = max(0.0, float(timeout))
        deadline = self.clock() + timeout
        last_probe = None
        while True:
            probe = self._probe(
                deadline=deadline,
                expected_operation_id=expected_operation_id,
                minimum_generation=minimum_generation,
            )
            last_probe = probe
            health = probe.get("health") or {}
            operation = health.get("operation") or {}

            report = self._report_from_probe(probe, verbose=verbose)
            terminal_codes = {
                item["code"]
                for item in report["data"]["findings"]
                if item["severity"] == "error"
            }
            if (
                self.ip in {"127.0.0.1", "localhost", "::1"}
                and len(self._candidate_ports()) > 1
            ):
                # Other local Unity projects are expected to occupy adjacent
                # ports while the target Editor is reloading. They are skipped
                # candidates, not proof that the requested target changed.
                terminal_codes.discard("target.mismatch")
                terminal_codes.discard("target.unverified")
                if probe.get("unverified") or (
                    not probe.get("reachable") and probe.get("mismatches")
                ):
                    for candidate_only_code in {
                        "unity.unsupported",
                        "protocol.reliability_missing",
                        "journal.unwritable",
                        "editor.compile_failed",
                        "editor.refresh_failed",
                    }:
                        terminal_codes.discard(candidate_only_code)
            expectation_met = True
            if minimum_generation is not None:
                expectation_met = (
                    int(health.get("generation") or 0) >= int(minimum_generation)
                )
            operation_matches = True
            if expected_operation_id:
                operation_matches = operation.get("opId") == expected_operation_id
                expectation_met = (
                    expectation_met
                    and operation_matches
                    and operation.get("phase") == "ready"
                )
                if (
                    not operation_matches
                    or operation.get("phase")
                    in {"requested", "refreshing_assets", "compiling", "reloading"}
                ):
                    # A stale compile-failed flag from the previous generation
                    # must not terminate a refresh that has not reached its own
                    # terminal phase yet.
                    terminal_codes.discard("editor.compile_failed")
                    terminal_codes.discard("editor.refresh_failed")
            terminal = bool(terminal_codes & {
                "project.not_found",
                "package.not_installed",
                "version.mismatch",
                "target.mismatch",
                "target.unverified",
                "unity.unsupported",
                "protocol.reliability_missing",
                "journal.unwritable",
                "outbox.unwritable",
                "editor.compile_failed",
                "editor.refresh_failed",
            })

            if report["ok"] and expectation_met:
                report["summary"] = "Unity 2022 is ready."
                report["data"]["waitedSeconds"] = round(
                    max(0.0, timeout - max(0.0, deadline - self.clock())),
                    3,
                )
                return report
            confirmed_in_progress = bool(
                expected_operation_id
                and probe.get("reachable")
                and operation_matches
                and operation.get("phase")
                in {"requested", "refreshing_assets", "compiling", "reloading"}
            )
            if terminal:
                if (
                    expected_operation_id
                    and not (
                        operation_matches
                        and operation.get("phase") == "failed"
                    )
                ):
                    report["ok"] = False
                    report["type"] = (
                        "operation_in_progress"
                        if confirmed_in_progress
                        else "outcome_unknown"
                    )
                    report["exitCode"] = 4
                    report["summary"] = (
                        (
                            f"Refresh operation {expected_operation_id} is still "
                            f"{operation.get('phase')}."
                        )
                        if confirmed_in_progress
                        else
                        f"Refresh operation {expected_operation_id} has not "
                        "reached a confirmed terminal state. Inspect that same "
                        "operation before starting another refresh."
                    )
                return report
            if self.clock() >= deadline:
                report["ok"] = False
                if expected_operation_id:
                    report["type"] = (
                        "operation_in_progress"
                        if confirmed_in_progress
                        else "outcome_unknown"
                    )
                    report["exitCode"] = 4
                    report["summary"] = (
                        (
                            f"Timed out after {timeout:g}s while refresh operation "
                            f"{expected_operation_id} remained "
                            f"{operation.get('phase')}; do not start another "
                            "refresh while it is active."
                        )
                        if confirmed_in_progress
                        else
                        f"Timed out after {timeout:g}s before refresh operation "
                        f"{expected_operation_id} reached confirmed ready. "
                        "Inspect that same operation before starting another refresh."
                    )
                else:
                    report["type"] = "wait_timeout"
                    report["exitCode"] = 3
                    report["summary"] = (
                        f"Timed out after {timeout:g}s waiting for Unity 2022 to be ready."
                    )
                report["data"]["timedOut"] = True
                return report
            self.sleeper(min(poll_interval, max(0.0, deadline - self.clock())))
