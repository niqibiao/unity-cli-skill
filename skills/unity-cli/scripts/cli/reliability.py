"""Read-only reliability diagnostics for the Unity console service.

``doctor()`` reduces one health probe plus offline project/package checks to a
findings report; ``wait_ready()`` polls the same reduction until the service is
ready, a terminal defect appears, or the timeout lapses.

Both must work before the Unity package is installed, so the HTTP adapter stays
pure urllib instead of the package-resolved core client.
"""

import json
import time
import urllib.error
import urllib.request

from cli import DEFAULT_EDITOR_PORT
from cli.version_check import (
    get_package_version,
    get_plugin_version,
    is_aligned,
)

DEFAULT_POLL_INTERVAL = 0.5
HEARTBEAT_STALE_MS = 5000
# Consecutive polls that must look play-mode-deferred before reporting it when
# the service does not publish the Script Changes While Playing preference.
PLAY_MODE_DEBOUNCE_POLLS = 6
PROBE_TIMEOUT_SECONDS = 2.0

ACTIVE_PHASES = frozenset({
    "requested",
    "refreshing_assets",
    "compiling",
    "reloading",
})

# Findings that waiting longer cannot fix.
TERMINAL_CODES = frozenset({
    "project.not_found",
    "package.not_installed",
    "version.mismatch",
    "unity.unsupported",
    "editor.compile_failed",
    "editor.refresh_failed",
    "editor.play_mode_deferring_compile",
})

PLAY_MODE_DEFERRING_MESSAGE = (
    "Play mode is deferring the requested script compilation "
    "(Script Changes While Playing: Recompile After Finished Playing)."
)
PLAY_MODE_DEFERRING_REMEDIATION = (
    "Ask the user whether to exit play mode (runtime state is lost). After "
    "they approve, run `cs refresh --exit-playmode --wait` or exit play mode "
    "first via the editor/playmode.exit command."
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

    def post(self, port, endpoint, payload, timeout=PROBE_TIMEOUT_SECONDS):
        url = f"http://{self.ip}:{port}/CSharpConsole/{endpoint}"
        request = urllib.request.Request(
            url,
            data=_json_body(payload),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            if body:
                return body
            raise OSError(f"HTTP {error.code} {error.reason}") from error


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


def _health_is_ready(data):
    operation = data.get("operation") or {}
    phase = operation.get("phase") or ""
    heartbeat_age = data.get("mainThreadHeartbeatAgeMs")
    if isinstance(heartbeat_age, bool) or not isinstance(heartbeat_age, (int, float)):
        # Pre-2.1 packages do not publish the heartbeat; fall back to the
        # phase/flag checks alone (doctor reports health.heartbeat_missing).
        heartbeat_ready = True
    else:
        heartbeat_ready = 0 <= heartbeat_age <= HEARTBEAT_STALE_MS
    # isPlaying is deliberately not checked: play mode is a legitimate ready
    # state for executing commands.
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


def _classify_play_mode_deferral(data):
    """Classify a potentially play-mode-deferred compile.

    Returns ``"deferring"`` (preference says the compile waits for edit mode),
    ``"waiting"`` (Unity handles it by itself), ``"unknown"`` (deferral shape
    but no preference published — caller debounces), or ``None``.
    """
    operation = data.get("operation") or {}
    if operation.get("phase") not in ACTIVE_PHASES:
        return None
    if not operation.get("compileRequested") or not data.get("isPlaying"):
        return None
    preference = data.get("scriptChangesWhilePlaying")
    if preference == "recompile_after_finished":
        return "deferring"
    if preference in ("stop_and_recompile", "recompile_and_continue"):
        return "waiting"
    return "unknown"


class ReliabilityCoordinator:
    """Shared engine behind ``cs doctor`` and ``cs wait-ready``."""

    def __init__(
        self,
        project_root,
        *,
        package_dir=None,
        ip="127.0.0.1",
        port=DEFAULT_EDITOR_PORT,
        adapter=None,
        clock=None,
        sleeper=None,
    ):
        self.project_root = project_root
        self.package_dir = package_dir
        self.ip = ip or "127.0.0.1"
        self.port = int(port or DEFAULT_EDITOR_PORT)
        self.adapter = adapter or HttpConsoleAdapter(self.ip)
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep

    def _probe(self, timeout=PROBE_TIMEOUT_SECONDS):
        try:
            raw = self.adapter.post(self.port, "health", {}, timeout=timeout)
            envelope, data = _decode_envelope(raw)
        except Exception as error:
            return {
                "reachable": False,
                "port": self.port,
                "health": {},
                "error": str(error),
            }
        if not envelope.get("ok"):
            return {
                "reachable": False,
                "port": self.port,
                "health": {},
                "error": envelope.get("summary") or "health returned ok=false",
            }
        return {"reachable": True, "port": self.port, "health": data}

    def _report_from_probe(self, probe, *, verbose=False, play_mode_deferring=False):
        findings = []
        plugin_version = get_plugin_version()
        package_version = get_package_version(self.package_dir)

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

        if self.package_dir is None:
            findings.append(_finding(
                "package.not_installed",
                "error",
                "The C# Console package is not resolved.",
                "Run `cs setup` only after approving the exact package source.",
            ))
        else:
            findings.append(_finding(
                "package.installed",
                "info",
                "The C# Console package is resolved.",
                evidence={
                    "path": str(self.package_dir),
                    "version": package_version,
                } if verbose else {"version": package_version},
            ))
            if not is_aligned(plugin_version, package_version):
                findings.append(_finding(
                    "version.mismatch",
                    "error",
                    "unity-cli and the package are on different compatibility lines.",
                    "Align their major.minor versions, then re-run `cs setup`.",
                    {"plugin": plugin_version, "package": package_version},
                ))

        health = probe.get("health") or {}
        if not probe.get("reachable"):
            findings.append(_finding(
                "service.unreachable",
                "error",
                "The Unity service is not reachable.",
                "Start the Unity Editor on this project, then run `cs wait-ready`.",
                {"port": probe.get("port"), "error": probe.get("error")}
                if verbose else None,
            ))
        else:
            findings.append(_finding(
                "service.reachable",
                "info",
                "The Unity service is reachable.",
                evidence={"port": probe.get("port")},
            ))

            unity_version = health.get("unityVersion")
            if not (isinstance(unity_version, str) and unity_version.startswith("2022.")):
                findings.append(_finding(
                    "unity.unsupported",
                    "error",
                    "The connected Editor is not Unity 2022.",
                    "Open this project with Unity 2022.",
                    {"unityVersion": unity_version},
                ))

            heartbeat_age = health.get("mainThreadHeartbeatAgeMs")
            if isinstance(heartbeat_age, bool) or not isinstance(heartbeat_age, (int, float)):
                findings.append(_finding(
                    "health.heartbeat_missing",
                    "warning",
                    "The service does not publish a main-thread heartbeat; "
                    "readiness falls back to phase flags alone.",
                    "Update the C# Console package to 2.1 or later.",
                ))

            if play_mode_deferring:
                findings.append(_finding(
                    "editor.play_mode_deferring_compile",
                    "error",
                    PLAY_MODE_DEFERRING_MESSAGE,
                    PLAY_MODE_DEFERRING_REMEDIATION,
                    {
                        "scriptChangesWhilePlaying":
                            health.get("scriptChangesWhilePlaying"),
                        "phase": (health.get("operation") or {}).get("phase"),
                    },
                ))

            operation = health.get("operation") or {}
            if health.get("compileFailed"):
                findings.append(_finding(
                    "editor.compile_failed",
                    "error",
                    "Unity compilation failed.",
                    "Fix the compile errors, then run `cs refresh --wait`.",
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
                    "The Unity editor service is ready.",
                ))
            elif not play_mode_deferring:
                state = (
                    operation.get("message")
                    or health.get("editorState")
                    or "not ready"
                )
                findings.append(_finding(
                    "editor.not_ready",
                    "warning",
                    f"The Unity editor service is not ready: {state}.",
                    "Run `cs wait-ready` to wait without triggering a mutation.",
                ))

        errors = [item for item in findings if item["severity"] == "error"]
        ready = (
            bool(probe.get("reachable"))
            and _health_is_ready(health)
            and not errors
        )
        data = {
            "ready": ready,
            "port": probe.get("port"),
            "findings": findings,
        }
        if verbose and health:
            data["rawHealth"] = health

        if ready:
            summary = "The Unity editor service is ready."
            result_type = "ready"
            exit_code = 0
        else:
            not_ready = [
                item for item in findings
                if item["code"] == "editor.not_ready"
            ]
            summary = (
                errors[0]["summary"] if errors
                else not_ready[0]["summary"] if not_ready
                else "The Unity editor service is not ready."
            )
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

    def doctor(self, *, verbose=False):
        probe = self._probe()
        deferral = (
            _classify_play_mode_deferral(probe.get("health") or {})
            if probe.get("reachable")
            else None
        )
        report = self._report_from_probe(
            probe,
            verbose=verbose,
            play_mode_deferring=deferral == "deferring",
        )
        if deferral == "deferring":
            report["type"] = "play_mode_deferring_compile"
        return report

    def wait_ready(
        self,
        timeout,
        *,
        expected_operation_id=None,
        minimum_generation=None,
        exit_playmode_granted=False,
        verbose=False,
        poll_interval=DEFAULT_POLL_INTERVAL,
    ):
        timeout = max(0.0, float(timeout))
        deadline = self.clock() + timeout
        debounce = 0
        while True:
            probe = self._probe()
            health = probe.get("health") or {}
            operation = health.get("operation") or {}

            deferral = (
                _classify_play_mode_deferral(health)
                if probe.get("reachable")
                else None
            )
            debounce = debounce + 1 if deferral == "unknown" else 0
            deferring = not exit_playmode_granted and (
                deferral == "deferring"
                or (deferral == "unknown" and debounce >= PLAY_MODE_DEBOUNCE_POLLS)
            )

            report = self._report_from_probe(
                probe,
                verbose=verbose,
                play_mode_deferring=deferring,
            )

            terminal_codes = {
                item["code"]
                for item in report["data"]["findings"]
                if item["severity"] == "error"
            }
            operation_matches = True
            if expected_operation_id:
                operation_matches = operation.get("opId") == expected_operation_id
                if (
                    not operation_matches
                    or operation.get("phase") in ACTIVE_PHASES
                ):
                    # A stale compile-failed flag from a previous generation
                    # must not terminate a refresh that has not reached its own
                    # terminal phase yet.
                    terminal_codes.discard("editor.compile_failed")
                    terminal_codes.discard("editor.refresh_failed")
            terminal = bool(terminal_codes & TERMINAL_CODES)

            expectation_met = True
            if minimum_generation is not None:
                expectation_met = (
                    int(health.get("generation") or 0) >= int(minimum_generation)
                )
            if expected_operation_id:
                expectation_met = (
                    expectation_met
                    and operation_matches
                    and operation.get("phase") == "ready"
                )

            if report["ok"] and expectation_met:
                report["summary"] = "The Unity editor service is ready."
                report["data"]["waitedSeconds"] = round(
                    max(0.0, timeout - max(0.0, deadline - self.clock())),
                    3,
                )
                return report

            confirmed_in_progress = bool(
                expected_operation_id
                and probe.get("reachable")
                and operation_matches
                and operation.get("phase") in ACTIVE_PHASES
            )
            if terminal:
                if deferring:
                    report["type"] = "play_mode_deferring_compile"
                    report["summary"] = (
                        f"{PLAY_MODE_DEFERRING_MESSAGE} "
                        f"{PLAY_MODE_DEFERRING_REMEDIATION}"
                    )
                elif (
                    expected_operation_id
                    and not (
                        operation_matches
                        and operation.get("phase") in ("ready", "failed")
                    )
                ):
                    # The defect is real but this operation never reached its
                    # own terminal phase, so its outcome stays unresolved.
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
                        f"Timed out after {timeout:g}s waiting for the Unity "
                        "editor service to be ready."
                    )
                report["data"]["timedOut"] = True
                return report

            self.sleeper(
                min(poll_interval, max(0.0, deadline - self.clock()))
            )
