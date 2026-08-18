import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path


CLI_DIR = Path(__file__).resolve().parent
SKILL_DIR = CLI_DIR.parent.parent
EVALS_DIR = SKILL_DIR / "evals"

REGISTRY_SNAPSHOT_PATH = (
    CLI_DIR / "local_fixtures" / "builtin_registry_snapshot.v1.json"
)
ROUTING_OVERLAY_PATH = CLI_DIR / "routing_overlay.json"
ROUTE_CASES_PATH = EVALS_DIR / "route_cases.json"
TRIGGER_QUERIES_PATH = EVALS_DIR / "trigger_queries.json"
EVALS_PATH = EVALS_DIR / "evals.json"
ROUTING_SCHEMA_PATH = EVALS_DIR / "routing-output.schema.json"
TRIGGER_SCHEMA_PATH = EVALS_DIR / "routing-trigger-output.schema.json"

sys.path.insert(0, str(SKILL_DIR / "scripts"))

from cli.command_preflight import prepare_command  # noqa: E402

ROUTE_IDS = {
    *(f"r{index:02d}" for index in range(1, 65)),
    *(f"c{index:02d}" for index in range(1, 11)),
}
TRIGGER_IDS = {f"t{index:02d}" for index in range(1, 21)}
REGISTRY_ONLY_CONTROL_IDS = {
    "command/registry.fingerprint",
    "command/registry.snapshot",
}
EVAL_FIELDS = {
    "id",
    "prompt",
    "expected_output",
    "files",
    "expectations",
}

# Match case declarations rather than incidental examples such as
# {"id":"t01"} or prose ranges such as r01-r08.
EMBEDDED_CASE_ID_RE = re.compile(
    r"(?mi)^\s*(?:[-*]\s*)?[`'\"]?([rct]\d{2})[`'\"]?\s*[:：-]"
)


def _load_json(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class RoutingEvalCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = _load_json(REGISTRY_SNAPSHOT_PATH)
        cls.overlay = _load_json(ROUTING_OVERLAY_PATH)
        cls.route_cases = _load_json(ROUTE_CASES_PATH)
        cls.trigger_queries = _load_json(TRIGGER_QUERIES_PATH)
        cls.evals = _load_json(EVALS_PATH)

    def test_route_case_ids_are_complete_and_unique(self):
        cases = self.route_cases["cases"]
        case_ids = [case["id"] for case in cases]

        self.assertEqual(74, len(cases))
        self.assertEqual(74, len(set(case_ids)), "route case IDs must be unique")
        self.assertEqual(ROUTE_IDS, set(case_ids))

    def test_route_oracle_records_use_one_closed_canonical_shape(self):
        expected_fields = {
            "protocol": {"kind", "commandId", "args"},
            "blocked": {
                "kind",
                "commandId",
                "args",
                "invoke",
                "reason",
            },
            "cli": {
                "kind",
                "subcommand",
                "argvContains",
                "usesInputFile",
            },
        }
        for case in self.route_cases["cases"]:
            expected = case["expected"]
            kind = expected["kind"]
            fields = set(expected)
            if kind == "protocol" and "session" in fields:
                fields.remove("session")
                self.assertIsInstance(expected["session"], str)
                self.assertTrue(expected["session"])
            with self.subTest(case_id=case["id"]):
                self.assertEqual({"id", "prompt", "expected", "tier"}, set(case))
                self.assertIn(case["tier"], {"core", "advanced", "control-plane"})
                self.assertEqual(expected_fields[kind], fields)
                self.assertNotIn("ns", expected)
                self.assertNotIn("action", expected)
                if kind in ("protocol", "blocked"):
                    self.assertRegex(
                        expected["commandId"],
                        r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
                    )
                    self.assertIsInstance(expected["args"], dict)
                else:
                    self.assertIsInstance(expected["argvContains"], list)
                    self.assertTrue(
                        all(
                            isinstance(value, str)
                            for value in expected["argvContains"]
                        )
                    )
                    self.assertIs(
                        False,
                        expected["usesInputFile"],
                    )

    def test_original_primary_cases_cover_current_registry_and_denies(self):
        primary_cases = [
            case for case in self.route_cases["cases"] if case["id"].startswith("r")
        ]
        primary_ids = [
            case["expected"]["commandId"]
            for case in primary_cases
        ]
        counts = Counter(primary_ids)
        denied_ids = {item["id"] for item in self.overlay["denyPolicy"]}
        registry_ids = {
            command["id"]
            for command in self.registry["builtin"]["commands"]
        }

        self.assertEqual(64, len(primary_cases))
        self.assertEqual(
            {"editor/status": 2, "gameobject/get": 2},
            {command_id: count for command_id, count in counts.items() if count > 1},
            "merged status/get contracts should absorb the two retired read aliases",
        )
        self.assertEqual(
            registry_ids - REGISTRY_ONLY_CONTROL_IDS,
            set(primary_ids) - denied_ids,
            "the original 59 prompts must cover every non-registry current contract",
        )

    def test_blocked_routes_match_deny_policy_and_never_invoke(self):
        denied_ids = {item["id"] for item in self.overlay["denyPolicy"]}
        blocked_cases = [
            case
            for case in self.route_cases["cases"]
            if case["expected"]["kind"] == "blocked"
        ]
        blocked_case_ids = {
            case["expected"]["commandId"]
            for case in blocked_cases
        }

        self.assertEqual(2, len(denied_ids))
        self.assertEqual(denied_ids, blocked_case_ids)
        for case in blocked_cases:
            with self.subTest(case_id=case["id"]):
                self.assertIs(
                    False,
                    case["expected"].get("invoke"),
                    "blocked routes must explicitly set invoke=false",
                )
                self.assertEqual(
                    "deny_policy",
                    case["expected"].get("reason"),
                )
                self.assertEqual({}, case["expected"].get("args"))
                self.assertEqual("advanced", case["tier"])

    def test_protocol_oracles_match_current_contracts_and_visibility_tiers(self):
        overlay_commands = self.overlay["commands"]
        for case in self.route_cases["cases"]:
            expected = case["expected"]
            with self.subTest(case_id=case["id"]):
                self.assertNotIn("ns", expected)
                self.assertNotIn("action", expected)
                if expected["kind"] != "protocol":
                    continue
                command_id = expected["commandId"]
                prepared = prepare_command(
                    self.registry,
                    command_id,
                    expected.get("args", {}),
                    session_id=expected.get("session"),
                )
                self.assertEqual(command_id, prepared["id"])
                self.assertEqual(
                    overlay_commands[command_id]["tier"],
                    case["tier"],
                    "oracle visibility tier must match the routing overlay",
                )

    def test_trigger_queries_are_balanced_and_unique(self):
        queries = self.trigger_queries
        query_ids = [query["id"] for query in queries]
        decisions = [query["should_trigger"] for query in queries]

        self.assertEqual(20, len(queries))
        self.assertEqual(20, len(set(query_ids)), "trigger query IDs must be unique")
        self.assertTrue(
            all(type(decision) is bool for decision in decisions),
            "should_trigger values must be JSON booleans",
        )
        self.assertEqual(10, sum(decisions))
        self.assertEqual(10, len(decisions) - sum(decisions))

    def test_output_schemas_are_closed_and_keep_route_and_trigger_shapes_separate(self):
        route_schema = _load_json(ROUTING_SCHEMA_PATH)
        trigger_schema = _load_json(TRIGGER_SCHEMA_PATH)

        def assert_structured_output_subset(node):
            if isinstance(node, dict):
                self.assertNotIn("oneOf", node)
                if node.get("type") == "object":
                    self.assertIs(False, node.get("additionalProperties"))
                    self.assertEqual(
                        set(node.get("properties", {})),
                        set(node.get("required", [])),
                    )
                for value in node.values():
                    assert_structured_output_subset(value)
            elif isinstance(node, list):
                for value in node:
                    assert_structured_output_subset(value)

        for schema in (route_schema, trigger_schema):
            self.assertEqual(["results"], schema["required"])
            self.assertIs(False, schema["additionalProperties"])
            self.assertEqual("array", schema["properties"]["results"]["type"])
            assert_structured_output_subset(schema)

        route_branches = route_schema["properties"]["results"]["items"]["anyOf"]
        self.assertEqual(4, len(route_branches))
        fields_by_kind = {}
        for branch in route_branches:
            kind = branch["properties"]["kind"]["const"]
            fields_by_kind.setdefault(kind, []).append(set(branch["required"]))
            self.assertEqual(set(branch["properties"]), set(branch["required"]))
        self.assertEqual(
            [
                {
                    "id",
                    "kind",
                    "commandId",
                    "args",
                    "tier",
                },
                {
                    "id",
                    "kind",
                    "commandId",
                    "args",
                    "tier",
                    "session",
                },
            ],
            fields_by_kind["protocol"],
        )
        self.assertEqual(
            [
                {
                    "id",
                    "kind",
                    "commandId",
                    "args",
                    "tier",
                    "invoke",
                    "reason",
                }
            ],
            fields_by_kind["blocked"],
        )
        self.assertEqual(
            [
                {
                    "id",
                    "kind",
                    "subcommand",
                    "argvContains",
                    "usesInputFile",
                    "tier",
                }
            ],
            fields_by_kind["cli"],
        )
        self.assertTrue(
            all(branch["additionalProperties"] is False for branch in route_branches)
        )
        for branch in route_branches:
            if "args" not in branch["properties"]:
                continue
            args_schema = branch["properties"]["args"]
            self.assertEqual("array", args_schema["type"])
            self.assertEqual(
                {"name", "valueJson"},
                set(args_schema["items"]["required"]),
            )
            self.assertIs(False, args_schema["items"]["additionalProperties"])

        trigger_item = trigger_schema["properties"]["results"]["items"]
        self.assertEqual({"id", "shouldTrigger"}, set(trigger_item["required"]))
        self.assertIs(False, trigger_item["additionalProperties"])

    def test_skill_creator_eval_schema_and_coverage(self):
        self.assertEqual({"skill_name", "evals"}, set(self.evals))
        self.assertEqual("unity-cli", self.evals["skill_name"])

        evals = self.evals["evals"]
        self.assertEqual(9, len(evals))
        self.assertEqual(
            len(evals),
            len({evaluation["id"] for evaluation in evals}),
            "eval IDs must be unique",
        )

        embedded_ids = []
        for evaluation in evals:
            with self.subTest(eval_id=evaluation.get("id")):
                self.assertEqual(EVAL_FIELDS, set(evaluation))
                self.assertIsInstance(evaluation["id"], int)
                self.assertIsInstance(evaluation["prompt"], str)
                self.assertTrue(evaluation["prompt"].strip())
                self.assertIsInstance(evaluation["expected_output"], str)
                self.assertTrue(evaluation["expected_output"].strip())
                self.assertIsInstance(evaluation["files"], list)
                self.assertIsInstance(evaluation["expectations"], list)
                self.assertTrue(evaluation["expectations"])
                self.assertTrue(
                    all(
                        isinstance(expectation, str) and expectation.strip()
                        for expectation in evaluation["expectations"]
                    )
                )

            embedded_ids.extend(
                EMBEDDED_CASE_ID_RE.findall(evaluation["prompt"])
            )

        expected_counts = Counter(
            case["id"] for case in self.route_cases["cases"]
        )
        expected_counts.update(
            query["id"] for query in self.trigger_queries
        )
        self.assertEqual(
            expected_counts,
            Counter(embedded_ids),
            "eval prompts must embed every route and trigger ID exactly once",
        )


if __name__ == "__main__":
    unittest.main()
