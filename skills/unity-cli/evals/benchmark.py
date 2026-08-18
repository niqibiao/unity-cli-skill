#!/usr/bin/env python3
"""Prepare and deterministically grade old/new unity-cli routing eval runs.

Executors only choose routes; they never connect to Unity.  The resulting strict
completion rate is therefore an offline proxy, not a live Effective Completion
Rate.  Live mutation/read-back coverage must be reported separately.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
ROUTE_CASES_PATH = HERE / "route_cases.json"
TRIGGER_QUERIES_PATH = HERE / "trigger_queries.json"
EVALS_PATH = HERE / "evals.json"
REGISTRY_SNAPSHOT_PATH = (
    SKILL_DIR
    / "scripts"
    / "cli"
    / "local_fixtures"
    / "builtin_registry_snapshot.v1.json"
)
CONFIG_DIRS = ("old_skill", "new_skill")
CONFIG_NAMES = {"old_skill": "without_skill", "new_skill": "with_skill"}
_CASE_INSENSITIVE_ENUMS = None
_ARGUMENT_DEFAULTS = None


def _read_json(path):
    return json.loads(Path(path).read_text("utf-8"))


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value):
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _strict_json_loads(text):
    return json.loads(
        text,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_nonfinite,
    )


def _read_json_strict(path):
    return _strict_json_loads(Path(path).read_text("utf-8"))


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")


def _case_ids(prompt):
    return re.findall(r"(?m)^(r\d{2}|c\d{2}|t\d{2}):", prompt)


def _eval_dir(workspace, eval_item):
    slug = {
        1: "editor",
        2: "objects",
        3: "components-materials",
        4: "prefabs",
        5: "scenes-assets",
        6: "capture-profiler",
        7: "control",
        8: "activation",
        9: "tests-scriptableobjects",
    }[eval_item["id"]]
    return Path(workspace) / f"eval-{eval_item['id']:03d}-{slug}"


def prepare(workspace):
    workspace = Path(workspace).resolve()
    evals = _read_json(EVALS_PATH)["evals"]
    workspace.mkdir(parents=True, exist_ok=True)
    for eval_item in evals:
        for config_dir in CONFIG_DIRS:
            run_dir = _eval_dir(workspace, eval_item) / config_dir / "run-1"
            (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
            metadata = {
                "eval_id": eval_item["id"],
                "eval_name": _eval_dir(workspace, eval_item).name,
                "prompt": eval_item["prompt"],
                "configuration": CONFIG_NAMES[config_dir],
                "case_ids": _case_ids(eval_item["prompt"]),
            }
            _write_json(run_dir / "eval_metadata.json", metadata)
            (run_dir / "prompt.txt").write_text(eval_item["prompt"] + "\n", "utf-8")
    _write_json(
        workspace / "run_metadata.json",
        {
            "schemaVersion": 1,
            "skill": "unity-cli",
            "baselineSkill": str(SKILL_DIR.parent / "unity-cli-workspace" / "skill-snapshot"),
            "candidateSkill": str(SKILL_DIR),
            "preparedAt": datetime.now(timezone.utc).isoformat(),
            "warning": "Route-only benchmark; not a live Unity Effective Completion Rate.",
        },
    )
    print(workspace)


def _expected_result_fields(case_id, route_oracle, trigger_oracle):
    if case_id.startswith("t"):
        if case_id not in trigger_oracle:
            return None
        return {"id", "shouldTrigger"}
    case = route_oracle.get(case_id)
    if case is None:
        return None
    expected = case["expected"]
    if expected["kind"] == "protocol":
        fields = {"id", "kind", "commandId", "args", "tier"}
        if "session" in expected:
            fields.add("session")
        return fields
    if expected["kind"] == "blocked":
        return {
            "id",
            "kind",
            "commandId",
            "args",
            "tier",
            "invoke",
            "reason",
        }
    return {
        "id",
        "kind",
        "subcommand",
        "argvContains",
        "usesInputFile",
        "tier",
    }


def _decode_args(value):
    if not isinstance(value, list):
        return None, "args must be an array"
    decoded = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"name", "valueJson"}:
            return (
                None,
                f"args[{index}] must have exactly name and valueJson fields",
            )
        name = item["name"]
        value_json = item["valueJson"]
        if not isinstance(name, str) or not name:
            return None, f"args[{index}].name must be a non-empty string"
        if name in decoded:
            return None, f"duplicate arg name: {name}"
        if not isinstance(value_json, str) or not value_json:
            return None, f"args[{index}].valueJson must be a non-empty string"
        try:
            decoded[name] = _strict_json_loads(value_json)
        except (json.JSONDecodeError, ValueError) as error:
            return None, f"args[{index}].valueJson is invalid JSON: {error}"
    return decoded, None


def _result_shape_error(result, case_id, route_oracle, trigger_oracle):
    if not isinstance(result, dict):
        return f"{case_id}: result must be an object"
    expected_fields = _expected_result_fields(
        case_id, route_oracle, trigger_oracle
    )
    if expected_fields is None:
        return f"{case_id}: unknown case id"
    actual_fields = set(result)
    if actual_fields != expected_fields:
        return (
            f"{case_id}: fields must be exactly {sorted(expected_fields)}; "
            f"got {sorted(actual_fields)}"
        )
    if result["id"] != case_id:
        return f"{case_id}: result id does not match its input position"
    if case_id.startswith("t"):
        if type(result["shouldTrigger"]) is not bool:
            return f"{case_id}: shouldTrigger must be a JSON boolean"
        return None

    expected = route_oracle[case_id]["expected"]
    if result["kind"] != expected["kind"]:
        return f"{case_id}: kind does not match the oracle result shape"
    if not isinstance(result["tier"], str):
        return f"{case_id}: tier must be a string"
    if expected["kind"] in ("protocol", "blocked"):
        if not isinstance(result["commandId"], str):
            return f"{case_id}: commandId must be a string"
        _, args_error = _decode_args(result["args"])
        if args_error:
            return f"{case_id}: {args_error}"
        if "session" in expected and not isinstance(result["session"], str):
            return f"{case_id}: session must be a string"
        if expected["kind"] == "blocked":
            if type(result["invoke"]) is not bool:
                return f"{case_id}: invoke must be a JSON boolean"
            if not isinstance(result["reason"], str):
                return f"{case_id}: reason must be a string"
    else:
        if not isinstance(result["subcommand"], str):
            return f"{case_id}: subcommand must be a string"
        if (
            not isinstance(result["argvContains"], list)
            or not all(
                isinstance(value, str) for value in result["argvContains"]
            )
        ):
            return f"{case_id}: argvContains must be an array of strings"
        if type(result["usesInputFile"]) is not bool:
            return f"{case_id}: usesInputFile must be a JSON boolean"
    return None


def _result_map(path, case_ids, route_oracle, trigger_oracle):
    try:
        payload = _read_json_strict(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, f"route.json is missing or invalid: {exc}"
    if not isinstance(payload, dict) or set(payload) != {"results"}:
        return {}, "route.json must be an object with exactly the results field"
    results = payload["results"]
    if not isinstance(results, list):
        return {}, "route.json needs a results array"
    actual_ids = [
        result.get("id") if isinstance(result, dict) else None
        for result in results
    ]
    errors = []
    if actual_ids != list(case_ids):
        errors.append(
            "results must preserve input order exactly; expected {} but got {}".format(
                list(case_ids), actual_ids
            )
        )
    mapped = {}
    for index, result in enumerate(results):
        if not isinstance(result, dict) or not isinstance(result.get("id"), str):
            errors.append(f"result {index}: every result needs a string id")
            continue
        if result["id"] in mapped:
            errors.append(f"duplicate result id: {result['id']}")
            continue
        mapped[result["id"]] = result
        if index < len(case_ids):
            shape_error = _result_shape_error(
                result,
                case_ids[index],
                route_oracle,
                trigger_oracle,
            )
            if shape_error:
                errors.append(shape_error)
    return mapped, "; ".join(errors) if errors else None


def _route_expected(case):
    expected = case["expected"]
    if expected["kind"] in ("protocol", "blocked"):
        return {
            "kind": expected["kind"],
            "commandId": expected["commandId"],
        }
    return {"kind": "cli", "subcommand": expected["subcommand"]}


def _route_actual(actual):
    if not isinstance(actual, dict):
        return None
    if actual.get("kind") in ("protocol", "blocked"):
        return {
            "kind": actual.get("kind"),
            "commandId": actual.get("commandId"),
        }
    if actual.get("kind") == "cli":
        return {"kind": "cli", "subcommand": actual.get("subcommand")}
    return {"kind": actual.get("kind")}


def _payload_expected(case):
    expected = case["expected"]
    if expected["kind"] in ("protocol", "blocked"):
        value = {"args": expected.get("args", {})}
        if "session" in expected:
            value["session"] = expected["session"]
        if expected["kind"] == "blocked":
            value["reason"] = "<non-empty deny-policy explanation>"
        return value
    return {
        "argvContains": expected["argvContains"],
        "usesInputFile": expected["usesInputFile"],
    }


def _payload_actual(case, actual):
    if not isinstance(actual, dict):
        return None
    if case["expected"]["kind"] in ("protocol", "blocked"):
        decoded_args, _ = _decode_args(actual.get("args"))
        value = {"args": decoded_args}
        if "session" in case["expected"]:
            value["session"] = actual.get("session")
        if case["expected"]["kind"] == "blocked":
            value["reason"] = actual.get("reason")
        return value
    return {
        "argvContains": actual.get("argvContains"),
        "usesInputFile": actual.get("usesInputFile"),
    }


def _case_insensitive_enums():
    """Return canonical values for wire enums the service parses case-insensitively."""
    global _CASE_INSENSITIVE_ENUMS
    if _CASE_INSENSITIVE_ENUMS is None:
        values = {}
        snapshot = _read_json(REGISTRY_SNAPSHOT_PATH)
        for command in snapshot["builtin"]["commands"]:
            route = (
                command["wire"]["commandNamespace"],
                command["wire"]["action"],
            )
            enum_args = {}
            for spec in command.get("arguments", []):
                if (
                    spec.get("allowedValuesIgnoreCase")
                    and spec.get("allowedValues")
                ):
                    enum_args[spec["name"]] = [
                        json.loads(choice)
                        for choice in spec["allowedValues"]
                    ]
            if enum_args:
                values[route] = enum_args
        _CASE_INSENSITIVE_ENUMS = values
    return _CASE_INSENSITIVE_ENUMS


def _argument_defaults():
    """Return package-owned defaults used to compare equivalent payloads."""
    global _ARGUMENT_DEFAULTS
    if _ARGUMENT_DEFAULTS is None:
        values = {}
        snapshot = _read_json(REGISTRY_SNAPSHOT_PATH)
        for command in snapshot["builtin"]["commands"]:
            defaults = {}
            for spec in command.get("arguments", []):
                if spec.get("hasDefault"):
                    defaults[spec["name"]] = json.loads(spec["defaultJson"])
            if defaults:
                values[tuple(command["id"].split("/", 1))] = defaults
        _ARGUMENT_DEFAULTS = values
    return _ARGUMENT_DEFAULTS


def _canonicalize_payload(case, payload):
    if not isinstance(payload, dict) or case["expected"]["kind"] != "protocol":
        return payload
    route = tuple(case["expected"]["commandId"].split("/", 1))
    enum_args = _case_insensitive_enums().get(route, {})
    defaults = _argument_defaults().get(route, {})
    args = payload.get("args")
    if not isinstance(args, dict):
        return payload
    normalized = dict(payload)
    normalized_args = dict(args)
    for name, value in defaults.items():
        normalized_args.setdefault(name, value)
    for name, choices in enum_args.items():
        value = normalized_args.get(name)
        if not isinstance(value, str):
            continue
        for choice in choices:
            if isinstance(choice, str) and value.casefold() == choice.casefold():
                normalized_args[name] = choice
                break
    normalized["args"] = normalized_args
    return normalized


def _policy_expected(case):
    value = {"tier": case["tier"]}
    if case["expected"]["kind"] == "blocked":
        value["invoke"] = False
    return value


def _policy_actual(case, actual):
    if not isinstance(actual, dict):
        return None
    value = {"tier": actual.get("tier")}
    if case["expected"]["kind"] == "blocked":
        value["invoke"] = actual.get("invoke")
    return value


def _grade_route_case(case, actual):
    expected_route = _route_expected(case)
    actual_route = _route_actual(actual)
    route_ok = actual_route == expected_route
    expected_payload = _payload_expected(case)
    actual_payload = _payload_actual(case, actual)
    expected_kind = case["expected"]["kind"]
    if expected_kind == "blocked":
        payload_ok = (
            route_ok
            and isinstance(actual_payload, dict)
            and actual_payload.get("args") == case["expected"].get("args", {})
            and isinstance(actual_payload.get("reason"), str)
            and bool(actual_payload["reason"].strip())
        )
    elif expected_kind == "cli":
        expected_argv = expected_payload["argvContains"]
        actual_argv = (
            actual_payload.get("argvContains")
            if isinstance(actual_payload, dict)
            else None
        )
        accepted_argv = [expected_argv, ["--offline", *expected_argv]]
        argv_ok = actual_argv in accepted_argv
        payload_ok = (
            route_ok
            and argv_ok
            and actual_payload.get("usesInputFile")
            is expected_payload["usesInputFile"]
        )
    else:
        payload_ok = route_ok and (
            _canonicalize_payload(case, actual_payload)
            == _canonicalize_payload(case, expected_payload)
        )
    expected_policy = _policy_expected(case)
    actual_policy = _policy_actual(case, actual)
    policy_ok = route_ok and actual_policy == expected_policy
    exact = route_ok and payload_ok and policy_ok
    evidence = {
        "expected": {
            "route": expected_route,
            "payload": expected_payload,
            "policy": expected_policy,
        },
        "actual": {
            "route": actual_route,
            "payload": actual_payload,
            "policy": actual_policy,
        },
    }
    return exact, route_ok, payload_ok, policy_ok, evidence


def _grade_trigger_case(case, actual):
    actual_value = actual.get("shouldTrigger") if isinstance(actual, dict) else None
    passed = actual_value is case["should_trigger"]
    return passed, {
        "expected": case["should_trigger"],
        "actual": actual_value,
    }


def _metric(passed, total):
    return passed / total if total else 0.0


def _grade_run(run_dir, case_ids, route_oracle, trigger_oracle):
    output_path = Path(run_dir) / "outputs" / "route.json"
    actual_map, parse_error = _result_map(
        output_path, case_ids, route_oracle, trigger_oracle
    )
    expectations = []
    route_passed = payload_passed = policy_passed = exact_passed = 0
    trigger_true = trigger_false = trigger_tp = trigger_tn = 0

    for case_id in case_ids:
        actual = actual_map.get(case_id)
        if case_id.startswith("t"):
            case = trigger_oracle[case_id]
            passed, evidence = _grade_trigger_case(case, actual)
            exact_passed += int(passed)
            if case["should_trigger"]:
                trigger_true += 1
                trigger_tp += int(passed)
            else:
                trigger_false += 1
                trigger_tn += int(passed)
            expectations.append(
                {
                    "text": f"{case_id}: activation classification matches the oracle",
                    "passed": passed,
                    "evidence": json.dumps(evidence, ensure_ascii=False),
                }
            )
        else:
            case = route_oracle[case_id]
            exact, route_ok, payload_ok, policy_ok, evidence = _grade_route_case(
                case, actual
            )
            exact_passed += int(exact)
            route_passed += int(route_ok)
            payload_passed += int(payload_ok)
            policy_passed += int(policy_ok)
            expectations.append(
                {
                    "text": f"{case_id}: first route, payload, and exposure policy are exact",
                    "passed": exact,
                    "evidence": json.dumps(evidence, ensure_ascii=False),
                }
            )

    shape_ok = parse_error is None
    expectations.insert(
        0,
        {
            "text": (
                "route.json has the strict result shape and preserves every "
                "requested case in input order"
            ),
            "passed": shape_ok,
            "evidence": parse_error or "strict shape accepted",
        },
    )
    total = len(case_ids)
    is_trigger = all(case_id.startswith("t") for case_id in case_ids)
    strict_passed = exact_passed if shape_ok else 0
    custom_metrics = {
        "strict_completion_proxy": _metric(strict_passed, total),
        "cases_passed": strict_passed,
        "cases_total": total,
    }
    if is_trigger:
        custom_metrics.update(
            {
                "activation_accuracy": _metric(exact_passed, total),
                "activation_recall": _metric(trigger_tp, trigger_true),
                "activation_specificity": _metric(trigger_tn, trigger_false),
                "false_positive_rate": 1.0 - _metric(trigger_tn, trigger_false),
            }
        )
    else:
        custom_metrics.update(
            {
                "first_route_accuracy": _metric(route_passed, total),
                "payload_validity": _metric(payload_passed, total),
                "exposure_policy_accuracy": _metric(policy_passed, total),
            }
        )

    passed_expectations = sum(item["passed"] for item in expectations)
    grading = {
        "expectations": expectations,
        "summary": {
            "passed": passed_expectations,
            "failed": len(expectations) - passed_expectations,
            "total": len(expectations),
            "pass_rate": _metric(passed_expectations, len(expectations)),
        },
        "execution_metrics": {
            "tool_calls": {},
            "total_tool_calls": 0,
            "total_steps": 1,
            "errors_encountered": 0 if parse_error is None else 1,
            "output_chars": output_path.stat().st_size if output_path.exists() else 0,
            "transcript_chars": 0,
        },
        "timing": {
            "executor_duration_seconds": 0.0,
            "grader_duration_seconds": 0.0,
            "total_duration_seconds": 0.0,
        },
        "claims": [],
        "user_notes_summary": {
            "uncertainties": [
                "Route-only benchmark; no Unity mutation or read-back was executed."
            ],
            "needs_review": [],
            "workarounds": [],
        },
    }
    _write_json(Path(run_dir) / "grading.json", grading)
    _write_json(Path(run_dir) / "outputs" / "score.json", custom_metrics)
    _write_json(
        Path(run_dir) / "outputs" / "benchmark_scope.json",
        {
            "kind": "offline-routing-proxy",
            "liveUnityExecuted": False,
            "effectiveCompletionRateClaimed": False,
        },
    )
    return grading, custom_metrics


def _stats(values):
    if not values:
        return {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return {
        "mean": mean,
        "stddev": math.sqrt(variance),
        "min": min(values),
        "max": max(values),
    }


def _load_oracles():
    route_oracle = {
        case["id"]: case for case in _read_json(ROUTE_CASES_PATH)["cases"]
    }
    trigger_oracle = {
        case["id"]: case for case in _read_json(TRIGGER_QUERIES_PATH)
    }
    return route_oracle, trigger_oracle


def grade_current(workspace):
    """Grade only fresh candidate outputs and report the strict 94-case micro rate."""
    workspace = Path(workspace).resolve()
    evals = _read_json(EVALS_PATH)["evals"]
    route_oracle, trigger_oracle = _load_oracles()
    embedded_case_ids = [
        case_id
        for eval_item in evals
        for case_id in _case_ids(eval_item["prompt"])
    ]
    if len(embedded_case_ids) != 94 or len(set(embedded_case_ids)) != 94:
        raise ValueError(
            "candidate routing benchmark must contain 94 unique embedded cases"
        )

    runs = []
    per_eval = []
    passed = 0
    total = 0
    for eval_item in evals:
        case_ids = _case_ids(eval_item["prompt"])
        run_dir = _eval_dir(workspace, eval_item) / "new_skill" / "run-1"
        grading, metrics = _grade_run(
            run_dir, case_ids, route_oracle, trigger_oracle
        )
        passed += metrics["cases_passed"]
        total += metrics["cases_total"]
        result = {
            "pass_rate": metrics["strict_completion_proxy"],
            "passed": metrics["cases_passed"],
            "failed": metrics["cases_total"] - metrics["cases_passed"],
            "total": metrics["cases_total"],
            "time_seconds": 0.0,
            "tokens": 0,
            "tool_calls": 0,
            "errors": grading["execution_metrics"]["errors_encountered"],
            "diagnostic_metrics": metrics,
        }
        runs.append(
            {
                "eval_id": eval_item["id"],
                "eval_name": _eval_dir(workspace, eval_item).name,
                "configuration": "current",
                "run_number": 1,
                "result": result,
                "expectations": grading["expectations"],
                "notes": [
                    "Offline route/payload/policy proxy; live Unity was not executed."
                ],
            }
        )
        per_eval.append(
            {
                "eval_id": eval_item["id"],
                "eval_name": _eval_dir(workspace, eval_item).name,
                "passed": metrics["cases_passed"],
                "total": metrics["cases_total"],
                "strict_micro_rate": metrics["strict_completion_proxy"],
            }
        )

    if total != 94:
        raise ValueError(f"candidate routing denominator changed: {total}, expected 94")
    strict_micro_rate = _metric(passed, total)
    benchmark = {
        "metadata": {
            "skill_name": "unity-cli",
            "skill_path": str(SKILL_DIR),
            "executor_model": "recorded by run provenance",
            "analyzer_model": "deterministic pure-stdlib grader",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evals_run": [item["id"] for item in evals],
            "runs_per_configuration": 1,
            "configuration_mapping": {
                "current": "fresh candidate checkout",
            },
            "scope": "offline first-route/payload/exposure-policy proxy",
        },
        "runs": runs,
        "run_summary": {
            "current": {
                "strict_micro_rate": strict_micro_rate,
                "passed": passed,
                "total": total,
                "macro_pass_rate": _stats(
                    [run["result"]["pass_rate"] for run in runs]
                ),
            }
        },
        "notes": [
            "Candidate-only acceptance; no baseline outputs are read.",
            "All 59 built-in actions, 10 collision cases, and 20 balanced activation queries are counted once.",
            "This benchmark did not mutate Unity and is not live Effective Completion Rate.",
        ],
    }
    _write_json(workspace / "benchmark.json", benchmark)
    _write_json(
        workspace / "summary.json",
        {
            "scope": "offline-routing-proxy",
            "liveUnityExecuted": False,
            "configuration": "current",
            "passed": passed,
            "total": total,
            "strict_micro_rate": strict_micro_rate,
            "evals": per_eval,
        },
    )
    lines = [
        "# unity-cli current command-routing acceptance",
        "",
        "> Candidate-only, offline routing proxy. No live Unity execution.",
        "",
        "| Eval | Passed | Total | Strict rate |",
        "|---|---:|---:|---:|",
    ]
    for item in per_eval:
        lines.append(
            f"| {item['eval_name']} | {item['passed']} | {item['total']} | "
            f"{item['strict_micro_rate']:.1%} |"
        )
    lines.extend(
        [
            "",
            f"Strict micro rate: {passed}/{total} ({strict_micro_rate:.1%})",
            "",
        ]
    )
    (workspace / "benchmark.md").write_text("\n".join(lines), "utf-8")
    print(workspace / "benchmark.json")


def grade(workspace):
    workspace = Path(workspace).resolve()
    evals = _read_json(EVALS_PATH)["evals"]
    route_oracle, trigger_oracle = _load_oracles()
    runs = []
    all_metrics = {name: [] for name in CONFIG_NAMES.values()}
    strict_case_totals = {
        name: {"passed": 0, "total": 0} for name in CONFIG_NAMES.values()
    }

    for eval_item in evals:
        case_ids = _case_ids(eval_item["prompt"])
        eval_name = _eval_dir(workspace, eval_item).name
        for config_dir in CONFIG_DIRS:
            configuration = CONFIG_NAMES[config_dir]
            run_dir = _eval_dir(workspace, eval_item) / config_dir / "run-1"
            grading, metrics = _grade_run(
                run_dir, case_ids, route_oracle, trigger_oracle
            )
            strict_case_totals[configuration]["passed"] += metrics["cases_passed"]
            strict_case_totals[configuration]["total"] += metrics["cases_total"]
            result = {
                "pass_rate": metrics["strict_completion_proxy"],
                "passed": metrics["cases_passed"],
                "failed": metrics["cases_total"] - metrics["cases_passed"],
                "total": metrics["cases_total"],
                "time_seconds": 0.0,
                "tokens": 0,
                "tool_calls": 0,
                "errors": grading["execution_metrics"]["errors_encountered"],
                "diagnostic_metrics": metrics,
            }
            all_metrics[configuration].append(result)
            runs.append(
                {
                    "eval_id": eval_item["id"],
                    "eval_name": eval_name,
                    "configuration": configuration,
                    "run_number": 1,
                    "result": result,
                    "expectations": grading["expectations"],
                    "notes": [
                        "Offline route/payload/policy proxy; live Unity was not executed."
                    ],
                }
            )

    summary = {}
    for configuration, results in all_metrics.items():
        summary[configuration] = {
            "pass_rate": _stats([result["pass_rate"] for result in results]),
            "time_seconds": _stats([0.0 for _ in results]),
            "tokens": _stats([0 for _ in results]),
        }
    before = summary["without_skill"]["pass_rate"]["mean"]
    after = summary["with_skill"]["pass_rate"]["mean"]
    summary["delta"] = {
        "pass_rate": f"{after - before:+.4f}",
        "time_seconds": "+0.0",
        "tokens": "+0",
    }
    before_micro = _metric(
        strict_case_totals["without_skill"]["passed"],
        strict_case_totals["without_skill"]["total"],
    )
    after_micro = _metric(
        strict_case_totals["with_skill"]["passed"],
        strict_case_totals["with_skill"]["total"],
    )
    micro_delta = after_micro - before_micro
    benchmark = {
        "metadata": {
            "skill_name": "unity-cli",
            "skill_path": str(SKILL_DIR),
            "executor_model": "Codex paired subagents (same session model)",
            "analyzer_model": "deterministic pure-stdlib grader",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evals_run": [item["id"] for item in evals],
            "runs_per_configuration": 1,
            "configuration_mapping": {
                "without_skill": "pre-optimization snapshot",
                "with_skill": "optimized skill",
            },
            "scope": "offline first-route/payload/exposure-policy proxy",
        },
        "runs": runs,
        "run_summary": summary,
        "notes": [
            "without_skill means the frozen pre-optimization skill snapshot; with_skill means the optimized skill.",
            "All 59 built-in actions, 10 collision cases, and 20 balanced activation queries are covered.",
            (
                "The viewer summary is an unweighted macro mean across 8 eval groups "
                f"({before:.1%} to {after:.1%}); the strict micro proxy over "
                f"{strict_case_totals['with_skill']['total']} cases is "
                f"{before_micro:.1%} to {after_micro:.1%} "
                f"({micro_delta * 100:+.1f} percentage points)."
            ),
            "This benchmark did not mutate Unity and must not be reported as live Effective Completion Rate.",
            "Timing and token metrics were not captured and are intentionally zero.",
        ],
    }
    _write_json(workspace / "benchmark.json", benchmark)

    detailed = {
        "scope": "offline-routing-proxy",
        "liveUnityExecuted": False,
        "configurations": {},
    }
    for configuration, totals in strict_case_totals.items():
        detailed["configurations"][configuration] = {
            **totals,
            "strict_micro_rate": _metric(totals["passed"], totals["total"]),
        }
    detailed["delta"] = micro_delta
    _write_json(workspace / "summary.json", detailed)

    lines = [
        "# unity-cli before/after command-routing benchmark",
        "",
        "> Scope: offline first-route/payload/exposure-policy proxy. No live Unity",
        "> mutation or read-back was executed; this is not live Effective Completion Rate.",
        "",
        "| Eval | Before | After | Delta |",
        "|---|---:|---:|---:|",
    ]
    for eval_item in evals:
        before_run = next(
            run for run in runs
            if run["eval_id"] == eval_item["id"]
            and run["configuration"] == "without_skill"
        )
        after_run = next(
            run for run in runs
            if run["eval_id"] == eval_item["id"]
            and run["configuration"] == "with_skill"
        )
        before_rate = before_run["result"]["pass_rate"]
        after_rate = after_run["result"]["pass_rate"]
        lines.append(
            f"| {_eval_dir(workspace, eval_item).name} | {before_rate:.1%} | "
            f"{after_rate:.1%} | {after_rate - before_rate:+.1%} |"
        )
    lines.extend(
        [
            "",
            f"Strict micro rate before: "
            f"{detailed['configurations']['without_skill']['strict_micro_rate']:.1%}",
            "",
            f"Strict micro rate after: "
            f"{detailed['configurations']['with_skill']['strict_micro_rate']:.1%}",
            "",
        ]
    )
    (workspace / "benchmark.md").write_text("\n".join(lines), "utf-8")
    print(workspace / "benchmark.json")


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "grade", "grade-current"):
        sub = subparsers.add_parser(name)
        sub.add_argument("workspace", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.workspace)
    elif args.command == "grade":
        grade(args.workspace)
    else:
        grade_current(args.workspace)


if __name__ == "__main__":
    main()
