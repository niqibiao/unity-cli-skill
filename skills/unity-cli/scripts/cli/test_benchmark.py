"""Regression tests for deterministic old/new routing benchmark grading."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CLI_DIR = Path(__file__).resolve().parent
SKILL_DIR = CLI_DIR.parent.parent
EVALS_DIR = SKILL_DIR / "evals"

SPEC = importlib.util.spec_from_file_location(
    "unity_cli_routing_benchmark",
    EVALS_DIR / "benchmark.py",
)
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


def _route_cases():
    return json.loads(
        (EVALS_DIR / "route_cases.json").read_text("utf-8")
    )["cases"]


def _trigger_queries():
    return json.loads(
        (EVALS_DIR / "trigger_queries.json").read_text("utf-8")
    )


def _candidate_result(case):
    expected = case["expected"]
    result = {"id": case["id"], "kind": expected["kind"]}
    if expected["kind"] in ("protocol", "blocked"):
        result.update(
            {
                "commandId": expected["commandId"],
                "args": [
                    {
                        "name": name,
                        "valueJson": json.dumps(
                            value,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                    for name, value in expected.get("args", {}).items()
                ],
                "tier": case["tier"],
            }
        )
        if "session" in expected:
            result["session"] = expected["session"]
        if expected["kind"] == "blocked":
            result["invoke"] = expected["invoke"]
            result["reason"] = expected["reason"]
    else:
        result.update(
            {
                "subcommand": expected["subcommand"],
                "argvContains": expected["argvContains"],
                "usesInputFile": expected["usesInputFile"],
                "tier": case["tier"],
            }
        )
    return result


def _write_current_outputs(workspace):
    route_oracle = {case["id"]: case for case in _route_cases()}
    trigger_oracle = {case["id"]: case for case in _trigger_queries()}
    evals = json.loads((EVALS_DIR / "evals.json").read_text("utf-8"))["evals"]
    for eval_item in evals:
        case_ids = BENCHMARK._case_ids(eval_item["prompt"])
        results = []
        for case_id in case_ids:
            if case_id.startswith("t"):
                results.append(
                    {
                        "id": case_id,
                        "shouldTrigger": trigger_oracle[case_id]["should_trigger"],
                    }
                )
            else:
                results.append(_candidate_result(route_oracle[case_id]))
        output_dir = (
            BENCHMARK._eval_dir(workspace, eval_item)
            / "new_skill"
            / "run-1"
            / "outputs"
        )
        output_dir.mkdir(parents=True)
        (output_dir / "route.json").write_text(
            json.dumps({"results": results}, ensure_ascii=False),
            "utf-8",
        )


class BenchmarkGradingTests(unittest.TestCase):
    def test_case_insensitive_service_enum_is_payload_equivalent(self):
        case = next(item for item in _route_cases() if item["id"] == "r38")
        actual = {
            "id": "r38",
            "kind": "protocol",
            "commandId": "project/scene.open",
            "args": [
                {
                    "name": "scenePath",
                    "valueJson": '"Assets/Scenes/UI.unity"',
                },
                {"name": "mode", "valueJson": '"Additive"'},
            ],
            "tier": case["tier"],
        }
        exact, route_ok, payload_ok, policy_ok, _ = BENCHMARK._grade_route_case(
            case, actual
        )
        self.assertEqual(
            (True, True, True, True),
            (exact, route_ok, payload_ok, policy_ok),
        )

    def test_extra_case_id_invalidates_strict_headline_score(self):
        case = next(item for item in _route_cases() if item["id"] == "r01")
        actual = {
            "id": case["id"],
            "kind": case["expected"]["kind"],
            "commandId": case["expected"]["commandId"],
            "args": [],
            "tier": case["tier"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            output_dir = run_dir / "outputs"
            output_dir.mkdir()
            (output_dir / "route.json").write_text(
                json.dumps(
                    {
                        "results": [
                            actual,
                            {
                                "id": "extra",
                                "kind": "protocol",
                                "ns": "editor",
                                "action": "status",
                                "args": {},
                                "tier": "core",
                            },
                        ]
                    }
                ),
                "utf-8",
            )
            grading, metrics = BENCHMARK._grade_run(
                run_dir,
                [case["id"]],
                {case["id"]: case},
                {},
            )

        self.assertFalse(grading["expectations"][0]["passed"])
        self.assertEqual(0, metrics["cases_passed"])
        self.assertEqual(0.0, metrics["strict_completion_proxy"])

    def test_strict_shape_rejects_legacy_fields_and_unexpected_fields(self):
        case = next(item for item in _route_cases() if item["id"] == "r01")
        result = _candidate_result(case)
        command_id = result.pop("commandId")
        result["ns"], result["action"] = command_id.split("/", 1)
        result["commentary"] = "legacy shape"

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            output_dir = run_dir / "outputs"
            output_dir.mkdir()
            (output_dir / "route.json").write_text(
                json.dumps({"results": [result]}),
                "utf-8",
            )
            grading, metrics = BENCHMARK._grade_run(
                run_dir,
                [case["id"]],
                {case["id"]: case},
                {},
            )

        self.assertFalse(grading["expectations"][0]["passed"])
        self.assertIn("fields", grading["expectations"][0]["evidence"])
        self.assertEqual(0, metrics["cases_passed"])
        self.assertEqual(1, metrics["cases_total"])

    def test_strict_shape_requires_input_order(self):
        cases = [
            next(item for item in _route_cases() if item["id"] == case_id)
            for case_id in ("r01", "r02")
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            output_dir = run_dir / "outputs"
            output_dir.mkdir()
            (output_dir / "route.json").write_text(
                json.dumps(
                    {"results": [_candidate_result(case) for case in reversed(cases)]}
                ),
                "utf-8",
            )
            grading, metrics = BENCHMARK._grade_run(
                run_dir,
                [case["id"] for case in cases],
                {case["id"]: case for case in cases},
                {},
            )

        self.assertFalse(grading["expectations"][0]["passed"])
        self.assertIn("input order", grading["expectations"][0]["evidence"])
        self.assertEqual(0, metrics["cases_passed"])
        self.assertEqual(2, metrics["cases_total"])

    def test_blocked_result_accepts_compact_denial_with_nonempty_reason(self):
        case = next(item for item in _route_cases() if item["id"] == "r05")
        result = _candidate_result(case)
        result["reason"] = "Arbitrary menu actions are deny-policy."
        result["args"] = []

        exact, route_ok, payload_ok, policy_ok, _ = BENCHMARK._grade_route_case(
            case, result
        )

        self.assertEqual(
            (True, True, True, True),
            (exact, route_ok, payload_ok, policy_ok),
        )

    def test_blocked_result_still_requires_a_nonempty_reason(self):
        case = next(item for item in _route_cases() if item["id"] == "r05")
        result = _candidate_result(case)
        result["reason"] = ""
        result["args"] = []

        exact, route_ok, payload_ok, policy_ok, _ = BENCHMARK._grade_route_case(
            case, result
        )

        self.assertEqual(
            (False, True, False, True),
            (exact, route_ok, payload_ok, policy_ok),
        )

    def test_protocol_payload_omitted_contract_default_is_equivalent(self):
        case = next(item for item in _route_cases() if item["id"] == "r32")
        result = _candidate_result(case)
        result["args"] = [
            item for item in result["args"] if item["name"] != "gameObjectPath"
        ]

        exact, route_ok, payload_ok, policy_ok, _ = BENCHMARK._grade_route_case(
            case, result
        )

        self.assertEqual(
            (True, True, True, True),
            (exact, route_ok, payload_ok, policy_ok),
        )

    def test_cli_argv_contains_allows_additional_session_safe_flags(self):
        case = next(item for item in _route_cases() if item["id"] == "c09")
        result = _candidate_result(case)
        result["argvContains"] = [
            "--offline",
            "--view",
            "custom",
            "--json",
        ]

        exact, route_ok, payload_ok, policy_ok, _ = BENCHMARK._grade_route_case(
            case, result
        )

        self.assertEqual(
            (True, True, True, True),
            (exact, route_ok, payload_ok, policy_ok),
        )

    def test_cli_argv_contains_rejects_refresh(self):
        case = next(item for item in _route_cases() if item["id"] == "c09")
        result = _candidate_result(case)
        result["argvContains"] = [
            "--refresh",
            "--view",
            "custom",
            "--json",
        ]

        exact, route_ok, payload_ok, policy_ok, _ = BENCHMARK._grade_route_case(
            case, result
        )

        self.assertEqual(
            (False, True, False, True),
            (exact, route_ok, payload_ok, policy_ok),
        )

    def test_cli_argv_contains_rejects_project_override(self):
        case = next(item for item in _route_cases() if item["id"] == "c09")
        result = _candidate_result(case)
        result["argvContains"] = [
            "--view",
            "custom",
            "--json",
            "--project",
            "C:/other",
        ]

        exact, route_ok, payload_ok, policy_ok, _ = BENCHMARK._grade_route_case(
            case, result
        )

        self.assertEqual(
            (False, True, False, True),
            (exact, route_ok, payload_ok, policy_ok),
        )

    def test_args_require_unique_names_and_valid_json_values(self):
        case = next(item for item in _route_cases() if item["id"] == "r21")
        result = _candidate_result(case)
        result["args"].append(
            {"name": "path", "valueJson": '"duplicate"'}
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            output_dir = run_dir / "outputs"
            output_dir.mkdir()
            (output_dir / "route.json").write_text(
                json.dumps({"results": [result]}),
                "utf-8",
            )
            grading, metrics = BENCHMARK._grade_run(
                run_dir,
                [case["id"]],
                {case["id"]: case},
                {},
            )

        self.assertFalse(grading["expectations"][0]["passed"])
        self.assertIn("duplicate arg name", grading["expectations"][0]["evidence"])
        self.assertEqual(0, metrics["cases_passed"])

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_values(self):
        case = next(item for item in _route_cases() if item["id"] == "r01")
        payloads = (
            (
                '{"results":[{"id":"r01","id":"r01","kind":"protocol",'
                '"commandId":"editor/status","args":[],"tier":"core"}]}'
            ),
            (
                '{"results":[{"id":"r01","kind":"protocol",'
                '"commandId":"editor/status","args":['
                '{"name":"unexpected","valueJson":"NaN"}],"tier":"core"}]}'
            ),
        )

        for payload in payloads:
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as temp_dir:
                    run_dir = Path(temp_dir)
                    output_dir = run_dir / "outputs"
                    output_dir.mkdir()
                    (output_dir / "route.json").write_text(payload, "utf-8")
                    grading, metrics = BENCHMARK._grade_run(
                        run_dir,
                        [case["id"]],
                        {case["id"]: case},
                        {},
                    )

                self.assertFalse(grading["expectations"][0]["passed"])
                self.assertEqual(0, metrics["cases_passed"])

    def test_candidate_only_grade_is_strict_89_case_micro_and_ignores_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_current_outputs(workspace)
            stale_baseline = (
                workspace
                / "eval-001-editor"
                / "old_skill"
                / "run-1"
                / "outputs"
            )
            stale_baseline.mkdir(parents=True)
            (stale_baseline / "route.json").write_text(
                '{"results":[{"id":"not-a-case"}]}',
                "utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(EVALS_DIR / "benchmark.py"),
                    "grade-current",
                    str(workspace),
                ],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            summary = json.loads(
                (workspace / "summary.json").read_text("utf-8")
            )
            benchmark = json.loads(
                (workspace / "benchmark.json").read_text("utf-8")
            )

        self.assertEqual("current", summary["configuration"])
        self.assertEqual(89, summary["passed"])
        self.assertEqual(89, summary["total"])
        self.assertEqual(1.0, summary["strict_micro_rate"])
        self.assertEqual(
            {"current"},
            {run["configuration"] for run in benchmark["runs"]},
        )
        self.assertEqual(8, len(benchmark["runs"]))

    def test_candidate_only_grade_keeps_89_denominator_after_one_wrong_answer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            _write_current_outputs(workspace)
            route_path = (
                workspace
                / "eval-001-editor"
                / "new_skill"
                / "run-1"
                / "outputs"
                / "route.json"
            )
            payload = json.loads(route_path.read_text("utf-8"))
            payload["results"][0]["commandId"] = "editor/playmode"
            route_path.write_text(json.dumps(payload), "utf-8")

            BENCHMARK.grade_current(workspace)
            summary = json.loads(
                (workspace / "summary.json").read_text("utf-8")
            )

        self.assertEqual(88, summary["passed"])
        self.assertEqual(89, summary["total"])
        self.assertAlmostEqual(88 / 89, summary["strict_micro_rate"])


if __name__ == "__main__":
    unittest.main()
