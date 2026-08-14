#!/usr/bin/env python3
"""Run a guarded live Unity command-contract completion suite.

This is deliberately not an agent-routing benchmark. It sends known-good
framework command payloads to a disposable Unity project, then verifies the
result through independent command readback or host file inspection.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
REPO_DIR = SKILL_DIR.parents[1]
CLI = SKILL_DIR / "scripts" / "cli" / "cs.py"
REGISTRY_SNAPSHOT = (
    SKILL_DIR
    / "scripts"
    / "cli"
    / "local_fixtures"
    / "builtin_registry_snapshot.v1.json"
)
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.registry_protocol import (  # noqa: E402
    validate_fingerprint,
    validate_snapshot,
)

EXPECTED_PROJECT_NAME = "ucp-test-codex"
EXPECTED_COMMAND_COUNT = 57
EXPECTED_AUTHORING_COUNT = 51
EXPECTED_CONTROL_COUNT = 6


class LiveSuiteError(RuntimeError):
    """A live-suite precondition, execution, or verification failure."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _safe_text(value, limit=1200):
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "…"


def _slug(text):
    return "".join(
        character.lower() if character.isalnum() else "-"
        for character in text
    ).strip("-")


def _result_json(envelope):
    data = envelope.get("data", {})
    if not isinstance(data, dict):
        return {}
    value = data.get("resultJson")
    if isinstance(value, dict):
        return value
    return data


def _component_names(components):
    names = []
    for component in components or []:
        if isinstance(component, str):
            names.append(component)
        elif isinstance(component, dict):
            names.append(
                str(
                    component.get("typeName")
                    or component.get("type")
                    or component.get("name")
                    or ""
                )
            )
    return names


def _has_component(names, short_name):
    return any(
        name == short_name
        or name.endswith("." + short_name)
        or name.endswith(short_name)
        for name in names
    )


def _find_named_node(nodes, name):
    pending = list(nodes or [])
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        if node.get("name") == name:
            return node
        children = node.get("children")
        if isinstance(children, list):
            pending.extend(children)
    return None


def _vector_matches(value, expected, tolerance=1e-3):
    if not isinstance(value, dict):
        return False
    for axis in ("x", "y", "z"):
        actual = value.get(axis)
        if not isinstance(actual, (int, float)):
            return False
        if not math.isclose(float(actual), float(expected[axis]), abs_tol=tolerance):
            return False
    return True


class LiveRunner:
    def __init__(
        self,
        project,
        package_repo,
        expected_port,
        expected_cli_revision,
        expected_package_revision,
        workspace,
        summary_path,
        run_token,
    ):
        self.project = project.resolve()
        self.package_repo = package_repo.resolve()
        self.expected_port = expected_port
        self.expected_cli_revision = expected_cli_revision
        self.expected_package_revision = expected_package_revision
        self.workspace = workspace.resolve()
        self.summary_path = summary_path.resolve()
        self.run_token = run_token
        self.session = f"unity-cli-live-{run_token}"
        self.asset_root = f"Assets/__UnityCliE2E_{run_token}"
        self.asset_root_host = self.project / self.asset_root
        self.owner_asset = f"{self.asset_root}/owner-{run_token}.txt"
        self.owner_host = self.project / self.owner_asset
        self.object_prefix = f"__UnityCliE2E_{run_token}"
        self.scratch_root = (
            self.project / "Temp" / "CSharpConsole" / "AgentScratch" / run_token
        )
        self.preflight_dir = self.workspace / "_preflight"
        self.cleanup_dir = self.workspace / "_cleanup"
        self.step_number = 0
        self.command_calls = 0
        self.command_ids_exercised = set()
        self.live_objects = []
        self.profiler_may_be_enabled = False
        self.owns_asset_root = False
        self.current_outputs = None
        self.current_diagnostics = None
        self.case_results = []
        self.cleanup_notes = []
        self.cleanup_ok = False
        self.contaminated = False
        self.started_at = _utc_now()
        self.cli_revision = subprocess.check_output(
            ["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
        ).strip()
        self.package_revision = subprocess.check_output(
            ["git", "-C", str(self.package_repo), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
        ).strip()

        committed = json.loads(REGISTRY_SNAPSHOT.read_text(encoding="utf-8"))
        self.committed_registry = validate_snapshot(
            committed,
            required_included="builtin",
        )
        self.registry_ids = {
            entry["id"]
            for entry in self.committed_registry["builtin"]["commands"]
        }

    def _output_dir(self):
        return self.current_outputs or self.preflight_dir

    def _record_process(self, label, command, completed, started, ended, request=None):
        output_dir = self._output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"{self.step_number:03d}-{_slug(label)}"
        if request is not None:
            _write_json(output_dir / f"{prefix}.request.json", request)
        (output_dir / f"{prefix}.stdout.txt").write_text(
            completed.stdout or "", encoding="utf-8"
        )
        (output_dir / f"{prefix}.stderr.txt").write_text(
            completed.stderr or "", encoding="utf-8"
        )
        _write_json(
            output_dir / f"{prefix}.process.json",
            {
                "argv": command,
                "returncode": completed.returncode,
                "started_at": started,
                "ended_at": ended,
            },
        )

    def _parse_process_json(self, label, completed):
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise LiveSuiteError(
                f"{label}: stdout is not JSON: {error}; stderr={completed.stderr!r}"
            ) from error
        if not isinstance(envelope, dict):
            raise LiveSuiteError(f"{label}: expected a JSON object")
        exit_code = envelope.get("exitCode")
        if isinstance(exit_code, int) and completed.returncode != exit_code:
            raise LiveSuiteError(
                f"{label}: process return code {completed.returncode} "
                f"!= envelope exitCode {exit_code}"
            )
        return envelope

    def _run(self, label, command, request=None, timeout=70):
        self.step_number += 1
        started = _utc_now()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            output_dir = self._output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            prefix = f"{self.step_number:03d}-{_slug(label)}"
            if request is not None:
                _write_json(output_dir / f"{prefix}.request.json", request)
            (output_dir / f"{prefix}.timeout.txt").write_text(
                f"subprocess timeout after {timeout}s\n"
                f"stdout={error.stdout!r}\nstderr={error.stderr!r}\n",
                encoding="utf-8",
            )
            raise LiveSuiteError(
                f"{label}: subprocess timed out after {timeout}s; "
                "the harness did not retry"
            ) from error
        ended = _utc_now()
        self._record_process(label, command, completed, started, ended, request)
        envelope = self._parse_process_json(label, completed)
        _write_json(
            self._output_dir()
            / f"{self.step_number:03d}-{_slug(label)}.response.json",
            envelope,
        )
        return envelope

    def health(self, label="health", output_dir=None):
        old_outputs = self.current_outputs
        if output_dir is not None:
            self.current_outputs = output_dir
        try:
            command = [
                sys.executable,
                "-B",
                str(CLI),
                "--project",
                str(self.project),
                "--port",
                str(self.expected_port),
                "--timeout",
                "30",
                "--json",
                "--verbose",
                "health",
            ]
            return self._run(label, command)
        finally:
            self.current_outputs = old_outputs

    def command(
        self,
        label,
        namespace,
        action,
        args=None,
        *,
        expect_ok=True,
        session=None,
    ):
        command_id = f"{namespace}/{action}"
        request = {"id": command_id, "args": args or {}}
        self.scratch_root.mkdir(parents=True, exist_ok=True)
        request_path = (
            self.scratch_root
            / f"{self.step_number + 1:03d}-{_slug(label)}.json"
        )
        _write_json(request_path, request)
        command = [
            sys.executable,
            "-B",
            str(CLI),
            "--project",
            str(self.project),
            "--port",
            str(self.expected_port),
            "--timeout",
            "30",
            "--session",
            session or self.session,
            "--json",
            "--verbose",
            "command",
            "--input",
            str(request_path),
        ]
        envelope = self._run(label, command, request=request)
        self.command_calls += 1
        self.command_ids_exercised.add(command_id)

        echoed_id = envelope.get("id")
        if echoed_id is not None and echoed_id != command_id:
            raise LiveSuiteError(
                f"{label}: response echoed id {echoed_id!r}, "
                f"expected {command_id!r}"
            )

        if expect_ok and not envelope.get("ok"):
            raise LiveSuiteError(
                f"{label}: {envelope.get('summary') or 'command failed'}"
            )
        return envelope

    def list_commands_cli(self, label, view, domains, tier):
        command = [
            sys.executable,
            "-B",
            str(CLI),
            "--project",
            str(self.project),
            "--port",
            str(self.expected_port),
            "--timeout",
            "30",
            "--session",
            self.session,
            "--json",
            "list-commands",
            "--view",
            view,
            "--tier",
            tier,
        ]
        for domain in domains:
            command.extend(["--domain", domain])
        return self._run(label, command)

    def require(self, condition, text, evidence):
        diagnostic = {
            "check": text,
            "passed": bool(condition),
            "evidence": _safe_text(evidence),
        }
        if self.current_diagnostics is not None:
            self.current_diagnostics.append(diagnostic)
        if not condition:
            raise LiveSuiteError(f"{text}: {diagnostic['evidence']}")

    def _validate_refresh_state(self):
        path = self.project / "Temp" / "CSharpConsole" / "refresh_state.json"
        if not path.is_file():
            raise LiveSuiteError(f"missing refresh state: {path}")
        state = json.loads(path.read_text(encoding="utf-8-sig"))
        if state.get("effectivePort") != self.expected_port:
            raise LiveSuiteError(
                "refresh state port "
                f"{state.get('effectivePort')} != expected {self.expected_port}"
            )
        if state.get("phase") != "ready":
            raise LiveSuiteError(f"Unity refresh phase is {state.get('phase')!r}")
        return state

    def _validate_health(self, envelope):
        if not envelope.get("ok"):
            raise LiveSuiteError(envelope.get("summary") or "health failed")
        data = envelope.get("data", {})
        expected = {
            "initialized": True,
            "isEditor": True,
            "port": self.expected_port,
            "refreshing": False,
            "editorState": "ready",
            "isCompiling": False,
            "compileFailed": False,
        }
        mismatches = {
            key: {"expected": value, "actual": data.get(key)}
            for key, value in expected.items()
            if data.get(key) != value
        }
        if mismatches:
            raise LiveSuiteError(f"health preconditions failed: {mismatches}")
        return data

    def prepare(self):
        if not self.cli_revision.startswith(self.expected_cli_revision):
            raise LiveSuiteError(
                f"CLI revision {self.cli_revision} does not match expected "
                f"{self.expected_cli_revision}"
            )
        if not self.package_revision.startswith(self.expected_package_revision):
            raise LiveSuiteError(
                f"package revision {self.package_revision} does not match "
                f"expected {self.expected_package_revision}"
            )
        package_status = subprocess.check_output(
            [
                "git",
                "-C",
                str(self.package_repo),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            text=True,
            encoding="utf-8",
        ).splitlines()
        if package_status:
            raise LiveSuiteError(
                f"package companion repository is dirty: {package_status}"
            )
        if self.project.name.lower() != EXPECTED_PROJECT_NAME:
            raise LiveSuiteError(
                f"refusing live mutations outside {EXPECTED_PROJECT_NAME}: "
                f"{self.project}"
            )
        if not (self.project / "Assets").is_dir():
            raise LiveSuiteError("project has no Assets directory")
        if not (self.project / "ProjectSettings").is_dir():
            raise LiveSuiteError("project has no ProjectSettings directory")
        if self.asset_root_host.exists():
            raise LiveSuiteError(f"run asset root already exists: {self.asset_root_host}")

        state = self._validate_refresh_state()
        health = self.health("preflight-health")
        health_data = self._validate_health(health)

        self.asset_root_host.mkdir(parents=False, exist_ok=False)
        self.owner_host.write_text(self.run_token + "\n", encoding="utf-8")
        self.owns_asset_root = True
        imported = self.command(
            "identity-owner-import",
            "project",
            "asset.import",
            {
                "assetPath": self.owner_asset,
                "forceSynchronousImport": True,
            },
        )
        imported_result = _result_json(imported)
        if not imported_result.get("imported") or not imported_result.get("exists"):
            raise LiveSuiteError(
                f"owner import did not confirm existence: {imported_result}"
            )
        self.verify_identity("identity-owner-readback")
        _write_json(
            self.workspace / "run_metadata.json",
            {
                "run_token": self.run_token,
                "project": str(self.project),
                "asset_root": self.asset_root,
                "object_prefix": self.object_prefix,
                "expected_port": self.expected_port,
                "cli_revision": self.cli_revision,
                "package_repository": str(self.package_repo),
                "package_revision": self.package_revision,
                "refresh_state": state,
                "health": health_data,
                "started_at": self.started_at,
                "metric": "Live Command Contract Completion Rate",
                "not_measured": "Natural-language skill activation and routing",
            },
        )

    def verify_identity(self, label, output_dir=None):
        old_outputs = self.current_outputs
        if output_dir is not None:
            self.current_outputs = output_dir
        try:
            self._validate_refresh_state()
            self._validate_health(self.health(label + "-health"))
            envelope = self.command(
                label + "-asset-list",
                "project",
                "asset.list",
                {"filter": f"owner-{self.run_token}", "folders": [self.asset_root]},
            )
            paths = _result_json(envelope).get("assetPaths", [])
            if self.owner_asset not in paths:
                raise LiveSuiteError(
                    f"identity sentinel missing from live project: {paths}"
                )
            if self.owner_host.read_text(encoding="utf-8").strip() != self.run_token:
                raise LiveSuiteError("identity owner file content changed")
        finally:
            self.current_outputs = old_outputs

    def create_gameobject(self, label, name, primitive_type="", parent_path=""):
        expected_path = f"{parent_path}/{name}" if parent_path else name
        self.live_objects.append({"path": expected_path})
        args = {"name": name}
        if primitive_type:
            args["primitiveType"] = primitive_type
        if parent_path:
            args["parentPath"] = parent_path
        envelope = self.command(label, "gameobject", "create", args)
        result = _result_json(envelope)
        self.require(result.get("name") == name, f"{label} returned requested name", result)
        self.require(
            isinstance(result.get("instanceId"), int),
            f"{label} returned an instance id",
            result,
        )
        self.live_objects[-1] = {"instanceId": result["instanceId"]}
        return result

    def _cleanup_object(self, selector):
        display = _safe_text(selector, 120)
        envelope = self.command(
            "cleanup-object-" + _slug(display),
            "gameobject",
            "destroy",
            selector,
            expect_ok=False,
        )
        if envelope.get("ok"):
            return
        readback = self.command(
            "cleanup-object-readback-" + _slug(display),
            "gameobject",
            "get",
            selector,
            expect_ok=False,
        )
        summary = str(readback.get("summary") or "").lower()
        confirmed_missing = (
            readback.get("ok") is False
            and readback.get("type") == "validation_error"
            and (
                "not found" in summary
                or "could not find" in summary
                or "no gameobject found" in summary
            )
        )
        if not confirmed_missing:
            raise LiveSuiteError(
                f"cleanup could not prove object absent {display}: "
                f"{_safe_text(readback)}"
            )

    def _cleanup_case_objects(self, initial_count):
        errors = []
        new_paths = self.live_objects[initial_count:]
        survivors = []
        for selector in reversed(new_paths):
            try:
                self._cleanup_object(selector)
            except Exception as error:
                errors.append(str(error))
                survivors.append(selector)
        self.live_objects[initial_count:] = list(reversed(survivors))
        if errors:
            raise LiveSuiteError("; ".join(errors))

    def run_case(self, eval_id, slug, title, prompt, function):
        run_dir = (
            self.workspace
            / f"eval-{eval_id:03d}-{slug}"
            / "with_skill"
            / "run-1"
        )
        outputs = run_dir / "outputs"
        outputs.mkdir(parents=True, exist_ok=True)
        _write_json(
            run_dir / "eval_metadata.json",
            {"eval_id": eval_id, "prompt": prompt, "title": title},
        )

        old_outputs = self.current_outputs
        old_diagnostics = self.current_diagnostics
        diagnostics = []
        self.current_outputs = outputs
        self.current_diagnostics = diagnostics
        initial_objects = len(self.live_objects)
        started_monotonic = time.monotonic()
        started_at = _utc_now()
        passed = True
        errors = []
        try:
            function()
        except Exception as error:
            passed = False
            errors.append(str(error))
        finally:
            try:
                self._cleanup_case_objects(initial_objects)
            except Exception as error:
                passed = False
                self.contaminated = True
                errors.append("cleanup: " + str(error))
            self.current_outputs = old_outputs
            self.current_diagnostics = old_diagnostics

        duration = time.monotonic() - started_monotonic
        evidence = (
            f"{len(diagnostics)} diagnostic checks passed; "
            f"{self.step_number} cumulative process steps."
            if passed
            else "; ".join(errors)
        )
        expectation = {
            "text": "End state matches the requested task",
            "passed": passed,
            "evidence": evidence,
        }
        grading = {
            "expectations": [expectation],
            "summary": {
                "passed": 1 if passed else 0,
                "failed": 0 if passed else 1,
                "total": 1,
                "pass_rate": 1.0 if passed else 0.0,
            },
        }
        result = {
            "eval_id": eval_id,
            "title": title,
            "passed": passed,
            "errors": errors,
            "diagnostics": diagnostics,
            "duration_seconds": round(duration, 3),
        }
        _write_json(outputs / "result.json", result)
        _write_json(run_dir / "grading.json", grading)
        _write_json(
            run_dir / "timing.json",
            {
                "started_at": started_at,
                "ended_at": _utc_now(),
                "total_duration_seconds": round(duration, 3),
            },
        )
        self.case_results.append({**result, "expectation": expectation})
        print(
            f"[{'PASS' if passed else 'FAIL'}] {eval_id:02d} {title}",
            flush=True,
        )

    def case_editor_state(self):
        editor = _result_json(
            self.command("editor-status", "editor", "status")
        )
        self.require(editor.get("initialized") is True, "Editor is initialized", editor)
        self.require(editor.get("port") == self.expected_port, "Editor port matches", editor)
        for field in ("isPlaying", "isPaused", "isCompiling"):
            self.require(
                type(editor.get(field)) is bool,
                f"{field} is a boolean in consolidated editor/status",
                {field: editor.get(field)},
            )
        self.require(
            editor.get("isPlaying") is False,
            "Suite starts in Edit Mode",
            editor,
        )

    def case_scene_hierarchy(self):
        root_name = self.object_prefix + "_HierarchyRoot"
        child_name = self.object_prefix + "_HierarchyChild"
        root = self.create_gameobject("hierarchy-create-root", root_name)
        child = self.create_gameobject(
            "hierarchy-create-child", child_name, parent_path=root["path"]
        )
        hierarchy = _result_json(
            self.command(
                "scene-hierarchy",
                "scene",
                "hierarchy",
                {"depth": 2, "includeComponents": True},
            )
        )
        root_node = _find_named_node(hierarchy.get("roots"), root_name)
        child_node = _find_named_node([root_node] if root_node else [], child_name)
        self.require(root_node is not None, "Hierarchy contains created root", hierarchy)
        self.require(child_node is not None, "Hierarchy contains created child", root_node)
        self.require(
            child_node.get("instanceId") == child.get("instanceId"),
            "Hierarchy child instance id matches create result",
            child_node,
        )

    def case_gameobject_roundtrip(self):
        name = self.object_prefix + "_Cube"
        created = self.create_gameobject("cube-create", name, primitive_type="Cube")
        readback = _result_json(
            self.command(
                "cube-get",
                "gameobject",
                "get",
                {"instanceId": created["instanceId"]},
            )
        )
        components = _component_names(readback.get("components"))
        self.require(readback.get("name") == name, "GameObject name round-trips", readback)
        for required in ("Transform", "MeshFilter", "MeshRenderer", "BoxCollider"):
            self.require(
                _has_component(components, required),
                f"Cube contains {required}",
                components,
            )

    def case_transform_roundtrip(self):
        name = self.object_prefix + "_Transform"
        created = self.create_gameobject("transform-create", name)
        position = {"x": 1.25, "y": 2.5, "z": -3.75}
        rotation = {"x": 10, "y": 45, "z": 20}
        scale = {"x": 1.5, "y": 0.75, "z": 2}
        self.command(
            "transform-set",
            "transform",
            "set",
            {
                "instanceId": created["instanceId"],
                "position": position,
                "rotation": rotation,
                "scale": scale,
                "local": True,
            },
        )
        readback = _result_json(
            self.command(
                "transform-get",
                "gameobject",
                "get",
                {"instanceId": created["instanceId"]},
            )
        ).get("transform", {})
        self.require(
            _vector_matches(readback.get("localPosition"), position),
            "Local position round-trips",
            readback.get("localPosition"),
        )
        self.require(
            _vector_matches(readback.get("localEulerAngles"), rotation, 1e-2),
            "Local rotation round-trips",
            readback.get("localEulerAngles"),
        )
        self.require(
            _vector_matches(readback.get("localScale"), scale),
            "Local scale round-trips",
            readback.get("localScale"),
        )

    def case_component_roundtrip(self):
        name = self.object_prefix + "_Component"
        created = self.create_gameobject("component-create", name)
        added = _result_json(
            self.command(
                "component-add-rigidbody",
                "component",
                "add",
                {
                    "typeName": "Rigidbody",
                    "gameObjectInstanceId": created["instanceId"],
                },
            )
        )
        readback = _result_json(
            self.command(
                "component-get-rigidbody",
                "component",
                "get",
                {
                    "typeName": "Rigidbody",
                    "gameObjectInstanceId": created["instanceId"],
                    "index": 0,
                },
            )
        )
        self.require(
            readback.get("componentInstanceId") == added.get("componentInstanceId"),
            "Added component instance id round-trips",
            {"added": added, "readback": readback},
        )
        removed = _result_json(
            self.command(
                "component-remove-rigidbody",
                "component",
                "remove",
                {
                    "typeName": "Rigidbody",
                    "gameObjectInstanceId": created["instanceId"],
                    "index": 0,
                },
            )
        )
        self.require(removed.get("removed") is True, "Rigidbody is removed", removed)
        missing = self.command(
            "component-readback-missing",
            "component",
            "get",
            {
                "typeName": "Rigidbody",
                "gameObjectInstanceId": created["instanceId"],
                "index": 0,
            },
            expect_ok=False,
        )
        self.require(
            missing.get("ok") is False,
            "Removed component no longer resolves",
            missing,
        )

    def case_material_roundtrip(self):
        asset_path = f"{self.asset_root}/LiveMaterial.mat"
        created = _result_json(
            self.command(
                "material-create",
                "material",
                "create",
                {"savePath": asset_path, "shaderName": "Standard"},
            )
        )
        readback = _result_json(
            self.command(
                "material-get",
                "material",
                "get",
                {"assetPath": asset_path},
            )
        )
        self.require(
            created.get("assetPath") == asset_path,
            "Material path matches request",
            created,
        )
        self.require(
            isinstance(readback.get("shaderName"), str)
            and bool(readback.get("shaderName")),
            "Material readback has a shader",
            readback,
        )
        self.require(
            readback.get("shaderName") == created.get("shaderName"),
            "Material shader round-trips",
            {"created": created, "readback": readback},
        )

    def case_prefab_asset_roundtrip(self):
        root_name = self.object_prefix + "_PrefabSource"
        child_name = self.object_prefix + "_PrefabChild"
        root = self.create_gameobject("prefab-source-create", root_name)
        self.create_gameobject(
            "prefab-child-create", child_name, primitive_type="Cube", parent_path=root["path"]
        )
        asset_path = f"{self.asset_root}/LivePrefab.prefab"
        created = _result_json(
            self.command(
                "prefab-create",
                "prefab",
                "create",
                {
                    "savePath": asset_path,
                    "gameObjectInstanceId": root["instanceId"],
                },
            )
        )
        hierarchy = _result_json(
            self.command(
                "prefab-asset-hierarchy",
                "prefab",
                "asset_hierarchy",
                {"assetPath": asset_path, "depth": 2, "includeComponents": True},
            )
        )
        child = _result_json(
            self.command(
                "prefab-asset-get-child",
                "prefab",
                "asset_get",
                {"assetPath": asset_path, "gameObjectPath": child_name},
            )
        )
        self.require(
            created.get("assetPath") == asset_path,
            "Prefab path matches request",
            created,
        )
        self.require(
            hierarchy.get("rootName") == created.get("name"),
            "Prefab hierarchy root matches create result",
            {"created": created, "hierarchy": hierarchy},
        )
        self.require(
            _find_named_node([hierarchy.get("root")], child_name) is not None,
            "Prefab hierarchy contains child",
            hierarchy,
        )
        self.require(
            child.get("name") == child_name,
            "Direct prefab child readback succeeds",
            child,
        )

    def case_prefab_instantiate(self):
        source_name = self.object_prefix + "_InstantiateSource"
        source = self.create_gameobject("instantiate-source-create", source_name)
        parent_name = self.object_prefix + "_InstantiateParent"
        parent = self.create_gameobject("instantiate-parent-create", parent_name)
        asset_path = f"{self.asset_root}/InstantiatePrefab.prefab"
        prefab_created = _result_json(
            self.command(
                "instantiate-prefab-create",
                "prefab",
                "create",
                {
                    "savePath": asset_path,
                    "gameObjectInstanceId": source["instanceId"],
                },
            )
        )
        position = {"x": 7, "y": 8, "z": 9}
        expected_instance_name = (
            prefab_created.get("name") or Path(asset_path).stem
        )
        pending_index = len(self.live_objects)
        self.live_objects.append(
            {"path": f"{parent['path']}/{expected_instance_name}"}
        )
        instantiated = _result_json(
            self.command(
                "prefab-instantiate",
                "prefab",
                "instantiate",
                {
                    "assetPath": asset_path,
                    "parentPath": parent["path"],
                    "position": position,
                },
            )
        )
        instance_id = instantiated.get("instanceId")
        if isinstance(instance_id, int):
            self.live_objects[pending_index] = {"instanceId": instance_id}
        readback = _result_json(
            self.command(
                "prefab-instance-get",
                "gameobject",
                "get",
                {"instanceId": instantiated["instanceId"]},
            )
        )
        transform = _result_json(
            self.command(
                "prefab-instance-transform",
                "gameobject",
                "get",
                {"instanceId": instantiated["instanceId"]},
            )
        ).get("transform", {})
        self.require(
            readback.get("instanceId") == instantiated.get("instanceId"),
            "Instantiated object identity round-trips",
            {"created": instantiated, "readback": readback},
        )
        self.require(
            _vector_matches(transform.get("position"), position),
            "Prefab instance world position round-trips",
            transform,
        )

    def case_selection_roundtrip(self):
        before = _result_json(
            self.command("selection-before", "project", "selection.get")
        )
        created = self.create_gameobject(
            "selection-create",
            self.object_prefix + "_Selected",
        )
        selected = _result_json(
            self.command(
                "selection-set",
                "project",
                "selection.set",
                {
                    "instanceIds": [created["instanceId"]],
                    "assetPaths": [],
                },
            )
        )
        readback = _result_json(
            self.command("selection-readback", "project", "selection.get")
        )
        self.require(
            selected.get("activeInstanceId") == created["instanceId"],
            "Selection mutation reports the created object",
            selected,
        )
        self.require(
            readback.get("activeInstanceId") == created["instanceId"],
            "Selection read-back identifies the created object",
            readback,
        )

        previous_objects = before.get("objects", [])
        previous_instance_ids = [
            item["instanceId"]
            for item in previous_objects
            if isinstance(item, dict)
            and isinstance(item.get("instanceId"), int)
            and not item.get("assetPath")
        ]
        previous_asset_paths = [
            item["assetPath"]
            for item in previous_objects
            if isinstance(item, dict)
            and isinstance(item.get("assetPath"), str)
            and item["assetPath"]
        ]
        self.command(
            "selection-restore",
            "project",
            "selection.set",
            {
                "instanceIds": previous_instance_ids,
                "assetPaths": previous_asset_paths,
            },
        )
        restored = _result_json(
            self.command("selection-restored", "project", "selection.get")
        )
        self.require(
            {
                item.get("instanceId")
                for item in restored.get("objects", [])
                if isinstance(item, dict)
            }
            == {
                item.get("instanceId")
                for item in previous_objects
                if isinstance(item, dict)
            },
            "Original Editor selection is restored",
            {"before": before, "restored": restored},
        )

    def case_profiler_lifecycle(self):
        initial = _result_json(
            self.command("profiler-initial-status", "profiler", "status")
        )
        if initial.get("enabled") is True:
            self.require(
                isinstance(initial.get("frameCount"), int)
                and initial["frameCount"] >= 0,
                "Pre-enabled Profiler status is valid",
                initial,
            )
            self.require(
                True,
                "Profiler mutation skipped to preserve pre-existing state",
                initial,
            )
            return
        self.require(
            initial.get("enabled") is False,
            "Profiler begins disabled or reports a valid enabled state",
            initial,
        )
        self.profiler_may_be_enabled = True
        started = _result_json(
            self.command(
                "profiler-start",
                "profiler",
                "start",
                {"deep": False, "logFile": ""},
            )
        )
        active = _result_json(
            self.command("profiler-active-status", "profiler", "status")
        )
        self.require(started.get("started") is True, "Profiler start is accepted", started)
        self.require(active.get("enabled") is True, "Profiler reports enabled", active)
        stopped = _result_json(
            self.command("profiler-stop", "profiler", "stop")
        )
        self.profiler_may_be_enabled = False
        final = _result_json(
            self.command("profiler-final-status", "profiler", "status")
        )
        self.require(stopped.get("stopped") is True, "Profiler stop is accepted", stopped)
        self.require(final.get("enabled") is False, "Profiler reports disabled", final)
        self.require(
            isinstance(final.get("frameCount"), int) and final["frameCount"] >= 0,
            "Profiler frame count is non-negative",
            final,
        )

    def case_command_registry(self):
        fingerprint = _result_json(
            self.command(
                "protocol-registry-fingerprint",
                "command",
                "registry.fingerprint",
            )
        )
        validate_fingerprint(fingerprint)
        live_snapshot = _result_json(
            self.command(
                "protocol-registry-snapshot",
                "command",
                "registry.snapshot",
                {"partition": "all"},
            )
        )
        validate_snapshot(
            live_snapshot,
            required_included=("builtin", "custom"),
            expected_fingerprint=fingerprint,
        )

        protocol = _result_json(
            self.command("protocol-command-list", "command", "list")
        )
        commands = protocol.get("commands", [])
        protocol_ids = {
            item.get("id") for item in commands if isinstance(item, dict)
        }
        self.require(
            len(commands) == EXPECTED_COMMAND_COUNT,
            "Protocol registry contains 57 commands",
            {"count": len(commands)},
        )
        self.require(
            len(protocol_ids) == EXPECTED_COMMAND_COUNT,
            "Protocol registry command ids are unique",
            sorted(protocol_ids),
        )
        self.require(
            protocol_ids == self.registry_ids,
            "Live protocol registry matches generated package snapshot",
            {
                "missing_live": sorted(self.registry_ids - protocol_ids),
                "extra_live": sorted(protocol_ids - self.registry_ids),
            },
        )
        self.require(
            live_snapshot["builtin"]["fingerprint"]
            == self.committed_registry["builtin"]["fingerprint"],
            "Live built-in fingerprint matches generated package snapshot",
            {
                "live": live_snapshot["builtin"]["fingerprint"],
                "committed": self.committed_registry["builtin"]["fingerprint"],
            },
        )
        self.require(
            {
                command["id"]
                for command in live_snapshot["builtin"]["commands"]
            }
            == self.registry_ids,
            "Live snapshot command contracts match generated package IDs",
            {"count": live_snapshot["builtin"]["count"]},
        )

        authoring_domains = (
            "editor",
            "scene",
            "objects",
            "assets",
            "prefabs",
            "capture",
        )
        cli_envelopes = {
            "authoring_core": self.list_commands_cli(
                "cli-list-authoring-core-live",
                "authoring",
                authoring_domains,
                "core",
            ),
            "authoring_advanced": self.list_commands_cli(
                "cli-list-authoring-advanced-live",
                "authoring",
                authoring_domains,
                "advanced",
            ),
            "control": self.list_commands_cli(
                "cli-list-control-live",
                "control",
                ("control",),
                "control-plane",
            ),
        }
        cli_partitions = {}
        for partition, cli_envelope in cli_envelopes.items():
            self.require(
                cli_envelope.get("ok") is True,
                "CLI live discovery succeeds",
                cli_envelope,
            )
            cli_data = cli_envelope.get("data", {})
            routes = (
                cli_data.get("routes", [])
                if isinstance(cli_data, dict)
                else []
            )
            cli_partitions[partition] = {
                item.get("id")
                for item in routes
                if isinstance(item, dict)
            }
        authoring_ids = (
            cli_partitions["authoring_core"]
            | cli_partitions["authoring_advanced"]
        )
        control_ids = cli_partitions["control"]
        self.require(
            len(authoring_ids) == EXPECTED_AUTHORING_COUNT,
            "CLI discovery contains 51 authoring commands",
            {
                "core": len(cli_partitions["authoring_core"]),
                "advanced": len(cli_partitions["authoring_advanced"]),
                "union": len(authoring_ids),
            },
        )
        self.require(
            len(control_ids) == EXPECTED_CONTROL_COUNT,
            "CLI discovery contains 6 control-plane commands",
            sorted(control_ids),
        )
        self.require(
            cli_partitions["authoring_core"].isdisjoint(
                cli_partitions["authoring_advanced"]
            )
            and authoring_ids.isdisjoint(control_ids),
            "CLI discovery partitions are mutually exclusive",
            {
                "core_advanced_overlap": sorted(
                    cli_partitions["authoring_core"]
                    & cli_partitions["authoring_advanced"]
                ),
                "authoring_control_overlap": sorted(
                    authoring_ids & control_ids
                ),
            },
        )
        cli_ids = authoring_ids | control_ids
        self.require(
            cli_ids == self.registry_ids,
            "CLI discovery matches generated package snapshot",
            {
                "count": len(cli_ids),
                "missing": sorted(self.registry_ids - cli_ids),
                "extra": sorted(cli_ids - self.registry_ids),
            },
        )

    def case_missing_session_reset(self):
        missing_session = f"unity-cli-absent-{self.run_token}"
        before = self.command(
            "session-inspect-before",
            "session",
            "inspect",
            {},
            expect_ok=False,
            session=missing_session,
        )
        before_result = _result_json(before)
        absent_before = (
            before.get("ok") is False or before_result.get("exists") is False
        )
        self.require(
            absent_before,
            "Fresh session is reported absent",
            before,
        )
        reset = _result_json(
            self.command(
                "session-reset-absent",
                "session",
                "reset",
                {},
                session=missing_session,
            )
        )
        self.require(
            reset.get("existed") is False,
            "Reset reports that the session did not exist",
            reset,
        )
        for field in ("hasCompilerAfter", "hasExecutorAfter"):
            self.require(
                reset.get(field) is False,
                f"{field} is false after reset",
                reset,
            )
        after = self.command(
            "session-inspect-after",
            "session",
            "inspect",
            {},
            expect_ok=False,
            session=missing_session,
        )
        after_result = _result_json(after)
        self.require(
            after.get("ok") is False or after_result.get("exists") is False,
            "Reset does not create the missing session",
            after,
        )

    def run_cases(self):
        cases = [
            (
                1,
                "editor-state",
                "Editor 状态一致性",
                "读取 Unity Editor 和 Play Mode 状态，并确认共享字段一致。",
                self.case_editor_state,
            ),
            (
                2,
                "scene-hierarchy",
                "场景层级读回",
                "创建父子对象，再从当前 Scene hierarchy 读回同一层级。",
                self.case_scene_hierarchy,
            ),
            (
                3,
                "gameobject",
                "GameObject 创建与组件读回",
                "创建 Cube，并确认对象身份与内置组件。",
                self.case_gameobject_roundtrip,
            ),
            (
                4,
                "transform",
                "Transform 写入与读回",
                "设置局部位置、旋转、缩放，再按容差读回。",
                self.case_transform_roundtrip,
            ),
            (
                5,
                "component",
                "Component 添加、读取、移除",
                "给对象添加 Rigidbody，读回同一组件，再移除并确认不存在。",
                self.case_component_roundtrip,
            ),
            (
                6,
                "material",
                "Material 创建与读回",
                "创建 Standard Material，并从 AssetDatabase 读回路径和 Shader。",
                self.case_material_roundtrip,
            ),
            (
                7,
                "prefab-asset",
                "Prefab 资产创建与直接读取",
                "从父子对象创建 Prefab，再直接读取 Prefab 层级和子节点。",
                self.case_prefab_asset_roundtrip,
            ),
            (
                8,
                "prefab-instance",
                "Prefab 实例化与位置读回",
                "创建并实例化 Prefab，再确认实例身份和世界坐标。",
                self.case_prefab_instantiate,
            ),
            (
                9,
                "selection-roundtrip",
                "Editor Selection 写入与恢复",
                "选择测试对象、独立读回，再恢复运行前的 Editor Selection。",
                self.case_selection_roundtrip,
            ),
            (
                10,
                "profiler",
                "Profiler 生命周期",
                "启动 Profiler、读取 enabled 状态、停止并再次确认。",
                self.case_profiler_lifecycle,
            ),
            (
                11,
                "command-registry",
                "Live command registry 对齐",
                "对比 Unity live registry、CLI discovery 与 package 生成的 57-command snapshot。",
                self.case_command_registry,
            ),
            (
                12,
                "session-control",
                "缺失 Session 的安全重置",
                "检查并重置一个全新的 session id，确认不会残留 compiler/executor。",
                self.case_missing_session_reset,
            ),
        ]
        for case in cases:
            self.run_case(*case)
            if self.contaminated:
                raise LiveSuiteError(
                    "case cleanup could not prove a clean state; "
                    "remaining live cases were not executed"
                )

    def _safe_host_asset_cleanup(self):
        if not self.asset_root_host.exists():
            return
        if not self.owns_asset_root:
            raise LiveSuiteError("run never established ownership of asset root")
        resolved = self.asset_root_host.resolve()
        assets = (self.project / "Assets").resolve()
        if resolved.parent != assets:
            raise LiveSuiteError(
                f"refusing host cleanup outside direct Assets child: {resolved}"
            )
        if self.run_token not in resolved.name:
            raise LiveSuiteError(f"cleanup path lacks run token: {resolved}")
        if not self.owner_host.is_file():
            raise LiveSuiteError("cleanup owner sentinel is missing")
        if self.owner_host.read_text(encoding="utf-8").strip() != self.run_token:
            raise LiveSuiteError("cleanup owner sentinel does not match run token")
        shutil.rmtree(resolved)
        meta = Path(str(resolved) + ".meta")
        if meta.is_file():
            meta.unlink()

    def _safe_scratch_cleanup(self):
        if not self.scratch_root.exists():
            return
        allowed_parent = (
            self.project / "Temp" / "CSharpConsole" / "AgentScratch"
        ).resolve()
        resolved = self.scratch_root.resolve()
        if resolved.parent != allowed_parent or resolved.name != self.run_token:
            raise LiveSuiteError(f"refusing scratch cleanup for {resolved}")
        shutil.rmtree(resolved)

    def cleanup(self):
        self.current_outputs = self.cleanup_dir
        errors = []
        try:
            identity_ok = False
            try:
                self.verify_identity("cleanup-identity", self.cleanup_dir)
                identity_ok = True
            except Exception as error:
                errors.append("identity recheck: " + str(error))

            if identity_ok:
                if self.profiler_may_be_enabled:
                    try:
                        stopped_envelope = self.command(
                            "cleanup-profiler-stop",
                            "profiler",
                            "stop",
                            expect_ok=False,
                        )
                        stopped = _result_json(stopped_envelope)
                        if (
                            not stopped_envelope.get("ok")
                            or stopped.get("stopped") is not True
                        ):
                            raise LiveSuiteError(
                                f"profiler/stop cleanup failed: "
                                f"{stopped_envelope}"
                            )
                        profiler_status = _result_json(
                            self.command(
                                "cleanup-profiler-readback",
                                "profiler",
                                "status",
                            )
                        )
                        if profiler_status.get("enabled") is not False:
                            raise LiveSuiteError(
                                f"Profiler remains enabled: {profiler_status}"
                            )
                        self.profiler_may_be_enabled = False
                    except Exception as error:
                        errors.append("profiler cleanup: " + str(error))

                survivors = []
                for selector in reversed(self.live_objects):
                    try:
                        self._cleanup_object(selector)
                    except Exception as error:
                        errors.append(
                            f"object cleanup {_safe_text(selector, 120)}: {error}"
                        )
                        survivors.append(selector)
                self.live_objects = list(reversed(survivors))

                try:
                    hierarchy = _result_json(
                        self.command(
                            "cleanup-scene-readback",
                            "scene",
                            "hierarchy",
                            {"depth": -1, "includeComponents": False},
                        )
                    )
                    remaining_names = []
                    pending = list(hierarchy.get("roots", []))
                    while pending:
                        node = pending.pop()
                        if not isinstance(node, dict):
                            continue
                        name = str(node.get("name") or "")
                        if self.run_token in name:
                            remaining_names.append(name)
                        children = node.get("children")
                        if isinstance(children, list):
                            pending.extend(children)
                    if remaining_names:
                        raise LiveSuiteError(
                            f"scene still contains run-token objects: "
                            f"{remaining_names}"
                        )
                except Exception as error:
                    errors.append("scene cleanup readback: " + str(error))

                if self.owns_asset_root:
                    try:
                        deleted = self.command(
                            "cleanup-asset-root",
                            "asset",
                            "delete",
                            {"assetPath": self.asset_root},
                            expect_ok=False,
                        )
                        result = _result_json(deleted)
                        if (
                            not deleted.get("ok")
                            or result.get("failedPaths")
                            or self.asset_root_host.exists()
                        ):
                            raise LiveSuiteError(
                                f"AssetDatabase cleanup incomplete: {result}"
                            )
                        self.cleanup_notes.append(
                            "Asset root deleted through asset/delete."
                        )
                    except Exception as error:
                        errors.append("AssetDatabase cleanup: " + str(error))

            if self.owns_asset_root and self.asset_root_host.exists():
                try:
                    self._safe_host_asset_cleanup()
                    self.cleanup_notes.append(
                        "Used verified host fallback for the unique asset root."
                    )
                except Exception as error:
                    errors.append("host asset cleanup: " + str(error))

            if identity_ok:
                try:
                    reset_envelope = self.command(
                        "cleanup-session-reset",
                        "session",
                        "reset",
                        {},
                        expect_ok=False,
                        session=self.session,
                    )
                    reset = _result_json(reset_envelope)
                    if (
                        not reset_envelope.get("ok")
                        or reset.get("hasCompilerAfter") is not False
                        or reset.get("hasExecutorAfter") is not False
                    ):
                        raise LiveSuiteError(
                            f"session/reset cleanup failed: {reset_envelope}"
                        )
                    inspect_envelope = self.command(
                        "cleanup-session-readback",
                        "session",
                        "inspect",
                        {},
                        expect_ok=False,
                        session=self.session,
                    )
                    inspect = _result_json(inspect_envelope)
                    if (
                        not inspect_envelope.get("ok")
                        or inspect.get("exists") is not False
                        or inspect.get("hasCompiler") is not False
                        or inspect.get("hasExecutor") is not False
                    ):
                        raise LiveSuiteError(
                            f"session cleanup readback failed: "
                            f"{inspect_envelope}"
                        )
                except Exception as error:
                    errors.append("session cleanup: " + str(error))
        finally:
            try:
                self._safe_scratch_cleanup()
            except Exception as error:
                errors.append("scratch cleanup: " + str(error))
            self.current_outputs = None

        self.cleanup_ok = not errors and not self.asset_root_host.exists()
        if errors:
            self.cleanup_notes.extend(errors)
        _write_json(
            self.cleanup_dir / "cleanup_result.json",
            {
                "ok": self.cleanup_ok,
                "asset_root_exists": self.asset_root_host.exists(),
                "remaining_object_selectors": self.live_objects,
                "notes": self.cleanup_notes,
            },
        )

    def write_reports(self):
        passed = sum(1 for result in self.case_results if result["passed"])
        total = len(self.case_results)
        rate = passed / total if total else 0.0
        rates = [1.0 if result["passed"] else 0.0 for result in self.case_results]
        durations = [result["duration_seconds"] for result in self.case_results]

        benchmark_runs = []
        for result in self.case_results:
            expectation = result["expectation"]
            benchmark_runs.append(
                {
                    "eval_id": result["eval_id"],
                    "eval_name": result["title"],
                    "configuration": "with_skill",
                    "run_number": 1,
                    "result": {
                        "pass_rate": 1.0 if result["passed"] else 0.0,
                        "passed": 1 if result["passed"] else 0,
                        "failed": 0 if result["passed"] else 1,
                        "total": 1,
                        "time_seconds": result["duration_seconds"],
                        "tokens": 0,
                        "tool_calls": 0,
                        "errors": 0 if result["passed"] else 1,
                    },
                    "expectations": [expectation],
                    "notes": [
                        "Known-good command payload executed against live Unity with readback.",
                        "This run does not measure natural-language activation or routing.",
                    ],
                }
            )

        benchmark = {
            "metadata": {
                "skill_name": "unity-cli live command contract",
                "skill_path": str(SKILL_DIR),
                "executor_model": "deterministic pure-stdlib live harness",
                "analyzer_model": "deterministic state/readback checks",
                "timestamp": _utc_now(),
                "evals_run": [result["eval_id"] for result in self.case_results],
                "runs_per_configuration": 1,
                "configuration_mapping": {
                    "with_skill": "current optimized CLI and command contracts"
                },
                "scope": "current-only live command contract completion",
            },
            "runs": benchmark_runs,
            "run_summary": {
                "with_skill": {
                    "pass_rate": {
                        "mean": rate,
                        "stddev": statistics.pstdev(rates) if rates else 0.0,
                        "min": min(rates) if rates else 0.0,
                        "max": max(rates) if rates else 0.0,
                    },
                    "time_seconds": {
                        "mean": statistics.mean(durations) if durations else 0.0,
                        "stddev": statistics.pstdev(durations) if durations else 0.0,
                        "min": min(durations) if durations else 0.0,
                        "max": max(durations) if durations else 0.0,
                    },
                    "tokens": {
                        "mean": 0.0,
                        "stddev": 0.0,
                        "min": 0,
                        "max": 0,
                    },
                }
            },
            "notes": [
                f"Current-only result: {passed}/{total} live tasks passed.",
                f"The suite made {self.command_calls} calls across "
                f"{len(self.command_ids_exercised)} unique command routes.",
                "No without_skill live baseline was run; no live before/after delta is claimed.",
                "This measures command execution plus independent readback, not skill activation or agent routing.",
                f"Cleanup {'succeeded' if self.cleanup_ok else 'FAILED'} for the unique run fixture.",
            ],
        }
        _write_json(self.workspace / "benchmark.json", benchmark)

        rows = []
        for result in self.case_results:
            status = "通过" if result["passed"] else "失败"
            detail = (
                f"{len(result['diagnostics'])} 项诊断检查"
                if result["passed"]
                else _safe_text(result["errors"], 240).replace("|", "\\|")
            )
            rows.append(
                f"| {result['eval_id']} | {result['title']} | {status} | {detail} |"
            )
        failures = [
            result for result in self.case_results if not result["passed"]
        ]
        failure_section = (
            "\n".join(
                f"- {result['title']}：{'; '.join(result['errors'])}"
                for result in failures
            )
            if failures
            else "- 无。"
        )
        cleanup_text = (
            "已清理，测试对象、资产根目录和 session 均未保留。"
            if self.cleanup_ok
            else "清理未完全成功，请查看 workspace 的 `_cleanup/cleanup_result.json`。"
        )
        markdown = f"""# unity-cli Live Command Contract 报告

## 一眼结论

当前版本在真实 Unity Editor 中完成 **{passed}/{total}** 个任务（**{rate:.1%}**）。

这个数字只回答一件事：**已知正确的 command JSON 发给 Unity 后，实际状态是否按要求改变，并能否读回验证。**

它不回答“用户一句自然语言能否触发 skill、选对 command 并生成正确参数”。所以这不是新的前后 ECR，也没有伪造 `without_skill` live baseline。自然语言路由仍由原来的 89-case offline benchmark 衡量。

## 每项结果

| # | 真实任务 | 结果 | 证据摘要 |
|---:|---|---|---|
{chr(10).join(rows)}

## 失败项

{failure_section}

## 运行边界

- 项目：`{self.project}`
- Unity 服务端口：`{self.expected_port}`
- CLI revision：`{self.cli_revision}`
- Package companion revision：`{self.package_revision}`
- 唯一 fixture：`{self.asset_root}` / `{self.object_prefix}_*`
- 实际 command 调用：{self.command_calls} 次，覆盖 {len(self.command_ids_exercised)} 个不同 route（含预检、读回、清理；不是 57 个 command 全部逐项执行）
- Live registry：检查 57 个注册 ID、fingerprint 与 package 生成 snapshot 完全一致
- 清理：{cleanup_text}

## 安全与可复现边界

- harness 在 mutation 前后都用端口、health 和唯一 owner asset 验证项目身份。
- 每次 subprocess 超时后直接失败，不由 harness 重发整个 command。
- CLI/package revision、请求、响应、读回与清理结果都保存在本次 workspace。

## 产物

- 详细 workspace：`{self.workspace}`
- 官方 skill-creator viewer 的 HTML 已从这个 workspace 生成。
- 每个任务只有一个顶层评分：`End state matches the requested task`；底层字段检查只作为诊断证据，不扩大分母。
"""
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_path.write_text(markdown, encoding="utf-8")
        _write_json(
            self.workspace / "suite_result.json",
            {
                "passed": passed,
                "failed": total - passed,
                "total": total,
                "rate": rate,
                "command_calls": self.command_calls,
                "unique_command_routes": sorted(self.command_ids_exercised),
                "cleanup_ok": self.cleanup_ok,
                "summary_path": str(self.summary_path),
                "ended_at": _utc_now(),
            },
        )
        return passed == total and self.cleanup_ok


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Run guarded live Unity command-contract completion checks."
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--package-repo", type=Path, required=True)
    parser.add_argument("--expected-port", type=int, required=True)
    parser.add_argument("--expect-cli", required=True)
    parser.add_argument("--expect-package", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument(
        "--confirm-disposable",
        action="store_true",
        help=f"Required; project basename must also be {EXPECTED_PROJECT_NAME}.",
    )
    parser.add_argument(
        "--run-token",
        default="",
        help="Optional safe alphanumeric token; generated when omitted.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    if not args.confirm_disposable:
        print("error: --confirm-disposable is required", file=sys.stderr)
        return 2
    token = args.run_token or datetime.now().strftime("r%Y%m%d%H%M%S")
    if not token.isalnum():
        print("error: --run-token must be alphanumeric", file=sys.stderr)
        return 2
    runner = LiveRunner(
        args.project,
        args.package_repo,
        args.expected_port,
        args.expect_cli,
        args.expect_package,
        args.workspace,
        args.summary,
        token,
    )
    args.workspace.mkdir(parents=True, exist_ok=False)
    suite_error = None
    try:
        runner.prepare()
        runner.run_cases()
    except Exception as error:
        suite_error = error
        print(f"[ABORT] {error}", file=sys.stderr, flush=True)
    finally:
        try:
            runner.cleanup()
        except Exception as error:
            runner.cleanup_ok = False
            runner.cleanup_notes.append("uncaught cleanup error: " + str(error))
            print(f"[CLEANUP ERROR] {error}", file=sys.stderr, flush=True)
    success = runner.write_reports()
    if suite_error is not None:
        return 2
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
