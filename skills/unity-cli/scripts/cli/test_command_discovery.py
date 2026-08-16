"""Local-only TDD coverage for progressive command discovery."""

import json
import sys
import unittest
from pathlib import Path


CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CLI_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.command_discovery import DiscoveryError, discover  # noqa: E402
from cli import registry_protocol as REGISTRY_PROTOCOL  # noqa: E402


def _contract(command_id, partition="builtin"):
    namespace, action = command_id.split("/", 1)
    return {
        "id": command_id,
        "wire": {
            "commandNamespace": namespace,
            "action": action,
        },
        "summary": f"Package summary for {command_id}",
        "partition": partition,
        "requirements": {
            "editor": True,
            "mainThread": True,
            "sessionId": False,
        },
        "arguments": [
            {
                "name": "name",
                "schema": {
                    "kind": "string",
                    "format": "",
                    "nullable": False,
                    "enumValues": [],
                    "fields": [],
                },
                "required": False,
                "hasDefault": True,
                "defaultJson": '""',
                "nonEmpty": False,
                "hasMinimum": False,
                "hasMaximum": False,
                "allowedValues": [],
                "allowedValuesIgnoreCase": False,
            }
        ],
        "result": {
            "kind": "object",
            "format": "",
            "nullable": False,
            "enumValues": [],
            "fields": [],
        },
        "rules": [],
    }


def _snapshot():
    builtin_ids = [
        "command/list",
        "editor/status",
        "object/create",
        "object/read",
        "scene/deep",
        "scene/shared",
    ]
    return {
        "schemaVersion": 1,
        "registryGeneration": "g",
        "builtin": {
            "included": True,
            "count": len(builtin_ids),
            "fingerprint": "b",
            "commands": [_contract(item) for item in sorted(builtin_ids)],
        },
        "custom": {
            "included": True,
            "count": 0,
            "fingerprint": "c",
            "commands": [],
        },
    }


def _snapshot_with_custom():
    snapshot = _snapshot()
    custom = _contract("mytools/do_thing", partition="custom")
    custom["summary"] = "Do the project-defined thing."
    custom["result"]["fields"] = [
        {
            "name": "count",
            "schema": {
                "kind": "integer",
                "format": "int32",
                "nullable": False,
                "enumValues": [],
                "fields": [],
            },
            "minimum": 1,
            "maximum": 10,
        },
        {
            "name": "mode",
            "schema": {
                "kind": "string",
                "format": "",
                "nullable": False,
                "enumValues": [],
                "fields": [],
            },
            "allowedValues": ["Fast", "Safe"],
            "allowedValuesIgnoreCase": True,
        },
    ]
    snapshot["custom"] = {
        "included": True,
        "count": 1,
        "fingerprint": "custom",
        "commands": [custom],
    }
    return snapshot


def _route(
    view,
    domains,
    tier,
    effect,
    select_when,
    *,
    prepare=(),
    verify=(),
    limitations=(),
):
    return {
        "view": view,
        "domains": list(domains),
        "tier": tier,
        "effect": effect,
        "selectWhen": select_when,
        "avoidWhen": "",
        "prepareWith": list(prepare),
        "verifyWith": list(verify),
        "limitations": list(limitations),
    }


def _overlay():
    return {
        "schemaVersion": 1,
        "expectedCounts": {
            "authoring": 5,
            "control": 1,
        },
        "domains": {
            "control": {
                "view": "control",
                "summary": "Registry and session mechanics.",
            },
            "editor": {
                "view": "authoring",
                "summary": "Editor lifecycle and diagnostics.",
            },
            "objects": {
                "view": "authoring",
                "summary": "Scene object authoring.",
            },
            "scene": {
                "view": "authoring",
                "summary": "Scene structure and persistence.",
            },
        },
        "commands": {
            "command/list": _route(
                "control",
                ["control"],
                "control-plane",
                "read",
                "Inspect command mechanics.",
            ),
            "editor/status": _route(
                "authoring",
                ["editor"],
                "core",
                "read",
                "Check Editor readiness.",
            ),
            "object/create": _route(
                "authoring",
                ["objects"],
                "core",
                "write",
                "Create a scene object.",
                prepare=["editor/status"],
                verify=["object/read"],
                limitations=["Creation completion must be read back."],
            ),
            "object/read": _route(
                "authoring",
                ["objects"],
                "core",
                "read",
                "Read a scene object.",
                verify=["scene/deep"],
            ),
            "scene/deep": _route(
                "authoring",
                ["scene"],
                "core",
                "read",
                "Inspect scene structure deeply.",
            ),
            "scene/shared": _route(
                "authoring",
                ["objects", "scene"],
                "core",
                "write",
                "Modify an object in a scene.",
                verify=["object/read"],
            ),
        },
        "denyPolicy": [
            {
                "id": "editor/menu.open",
                "domains": ["editor"],
                "tier": "advanced",
                "intent": "Invoke a menu item.",
                "reason": "Non-interactive modal risk.",
                "fallbackPolicy": "prohibited",
            }
        ],
    }


class ProgressiveDiscoveryTests(unittest.TestCase):
    def test_default_projection_is_compact_authoring_domain_index(self):
        result = discover(_snapshot(), overlay=_overlay())

        self.assertEqual("domain-index", result["kind"])
        self.assertEqual("authoring", result["view"])
        self.assertEqual(5, result["totalCommands"])
        self.assertEqual(
            ["editor", "objects", "scene"],
            [item["id"] for item in result["domains"]],
        )
        self.assertNotIn("commands", result)
        self.assertNotIn("control", [item["id"] for item in result["domains"]])

    def test_repeatable_multi_domain_returns_deduplicated_schema_free_cards(self):
        result = discover(
            _snapshot(),
            overlay=_overlay(),
            domains=["objects", "scene", "objects"],
            tier="core",
        )

        self.assertEqual("route-cards", result["kind"])
        self.assertEqual(["objects", "scene"], result["domains"])
        ids = [item["id"] for item in result["routes"]]
        self.assertEqual(
            ["object/create", "object/read", "scene/shared", "scene/deep"],
            ids,
        )
        self.assertEqual(len(ids), len(set(ids)))
        forbidden = {
            "arguments",
            "result",
            "rules",
            "wire",
            "requirements",
            "contract",
            "limitations",
            "prepareWith",
            "verifyWith",
        }
        for route in result["routes"]:
            self.assertTrue(forbidden.isdisjoint(route))

    def test_repeatable_multi_id_returns_full_selected_and_one_relation_layer(self):
        result = discover(
            _snapshot(),
            overlay=_overlay(),
            command_ids=[
                "object/create",
                "scene/shared",
                "object/create",
            ],
        )

        self.assertEqual("contract-bundle", result["kind"])
        self.assertEqual(
            ["object/create", "scene/shared"],
            [item["contract"]["id"] for item in result["selected"]],
        )
        self.assertEqual(
            ["editor/status", "object/read"],
            [item["contract"]["id"] for item in result["related"]],
        )
        self.assertNotIn(
            "scene/deep",
            [item["contract"]["id"] for item in result["related"]],
        )
        self.assertEqual(
            ["Creation completion must be read back."],
            result["selected"][0]["limitations"],
        )
        for item in result["selected"] + result["related"]:
            route = _overlay()["commands"][item["contract"]["id"]]
            self.assertEqual(route["domains"], item["domains"])
            self.assertEqual(route["tier"], item["tier"])
            self.assertEqual(route["effect"], item["effect"])
            self.assertIn("arguments", item["contract"])
            self.assertIn("result", item["contract"])
            self.assertIn("rules", item["contract"])
        self.assertEqual(
            [
                {
                    "source": "object/create",
                    "prepareWith": ["editor/status"],
                    "verifyWith": ["object/read"],
                },
                {
                    "source": "scene/shared",
                    "prepareWith": [],
                    "verifyWith": ["object/read"],
                },
            ],
            result["relations"],
        )

    def test_agent_contract_encoding_preserves_execution_semantics(self):
        compact = discover(
            _snapshot(),
            overlay=_overlay(),
            command_ids=["object/create", "scene/shared"],
        )
        package = discover(
            _snapshot(),
            overlay=_overlay(),
            command_ids=["object/create", "scene/shared"],
            contract_detail="package",
        )

        self.assertEqual("canonical-agent-v2", compact["contractEncoding"])
        self.assertEqual("package-v1", package["contractEncoding"])
        compact_contract = compact["selected"][0]["contract"]
        self.assertNotIn("wire", compact_contract)
        self.assertNotIn("summary", compact_contract)
        self.assertNotIn("partition", compact_contract)
        self.assertIn("requirements", compact_contract)
        self.assertIn("arguments", compact_contract)
        self.assertIn("result", compact_contract)
        self.assertIn("rules", compact_contract)
        self.assertNotIn("fields", compact_contract["result"])
        compact_bytes = len(
            json.dumps(compact, separators=(",", ":")).encode("utf-8")
        )
        package_bytes = len(
            json.dumps(package, separators=(",", ":")).encode("utf-8")
        )
        self.assertLess(compact_bytes, package_bytes)

    def test_mixed_or_unknown_selectors_fail_without_partial_projection(self):
        with self.assertRaisesRegex(DiscoveryError, "mutually exclusive"):
            discover(
                _snapshot(),
                overlay=_overlay(),
                domains=["objects"],
                command_ids=["object/read"],
            )
        with self.assertRaisesRegex(DiscoveryError, "unknown domain"):
            discover(
                _snapshot(),
                overlay=_overlay(),
                domains=["missing"],
            )
        with self.assertRaisesRegex(
            DiscoveryError,
            "unknown command",
        ) as unknown_command:
            discover(
                _snapshot(),
                overlay=_overlay(),
                command_ids=["missing/command"],
            )
        self.assertEqual("unknown_command", unknown_command.exception.code)
        denied = discover(
            _snapshot(),
            overlay=_overlay(),
            command_ids=["editor/menu.open"],
        )
        self.assertEqual("contract-bundle", denied["kind"])
        self.assertEqual([], denied["selected"])
        self.assertEqual(
            [
                {
                    "id": "editor/menu.open",
                    "domains": ["editor"],
                    "tier": "advanced",
                    "intent": "Invoke a menu item.",
                    "reason": "Non-interactive modal risk.",
                    "fallbackPolicy": "prohibited",
                    "invoke": False,
                }
            ],
            denied["denied"],
        )

    def test_overlay_command_missing_from_snapshot_is_registry_incomplete(self):
        snapshot = _snapshot()
        snapshot["builtin"]["commands"] = [
            command
            for command in snapshot["builtin"]["commands"]
            if command["id"] != "editor/status"
        ]
        snapshot["builtin"]["count"] -= 1

        with self.assertRaisesRegex(
            DiscoveryError,
            "routing overlay references unknown command",
        ) as incomplete:
            discover(
                snapshot,
                overlay=_overlay(),
                command_ids=["object/read"],
            )

        self.assertEqual("registry_incomplete", incomplete.exception.code)

    def test_mixed_executable_and_denied_ids_return_one_nonexecuting_bundle(self):
        result = discover(
            _snapshot(),
            overlay=_overlay(),
            command_ids=["editor/status", "editor/menu.open"],
        )

        self.assertEqual(
            ["editor/status"],
            [item["contract"]["id"] for item in result["selected"]],
        )
        self.assertEqual(
            ["editor/menu.open"],
            [item["id"] for item in result["denied"]],
        )
        self.assertFalse(result["denied"][0]["invoke"])

    def test_control_view_is_explicit_and_defaults_to_control_plane_tier(self):
        index = discover(
            _snapshot(),
            overlay=_overlay(),
            view="control",
        )
        self.assertEqual(1, index["totalCommands"])
        self.assertEqual(["control"], [item["id"] for item in index["domains"]])

        cards = discover(
            _snapshot(),
            overlay=_overlay(),
            view="control",
            domains=["control"],
        )
        self.assertEqual("control-plane", cards["tier"])
        self.assertEqual(["command/list"], [item["id"] for item in cards["routes"]])

    def test_custom_view_projects_live_or_cached_contracts_without_overlay_routes(self):
        snapshot = _snapshot_with_custom()
        index = discover(snapshot, overlay=_overlay(), view="custom")
        self.assertEqual(1, index["totalCommands"])
        self.assertEqual(["custom"], [item["id"] for item in index["domains"]])

        cards = discover(
            snapshot,
            overlay=_overlay(),
            view="custom",
            domains=["custom"],
        )
        self.assertEqual("custom", cards["tier"])
        self.assertEqual(
            ["mytools/do_thing"],
            [item["id"] for item in cards["routes"]],
        )
        self.assertNotIn("arguments", cards["routes"][0])

        bundle = discover(
            snapshot,
            overlay=_overlay(),
            view="custom",
            command_ids=["mytools/do_thing"],
        )
        self.assertEqual(
            ["mytools/do_thing"],
            [item["contract"]["id"] for item in bundle["selected"]],
        )
        self.assertEqual(["custom"], bundle["selected"][0]["domains"])
        self.assertEqual("custom", bundle["selected"][0]["tier"])
        self.assertEqual("project-defined", bundle["selected"][0]["effect"])
        self.assertEqual([], bundle["related"])
        self.assertEqual([], bundle["relations"])
        compact_fields = bundle["selected"][0]["contract"]["result"]["fields"]
        self.assertEqual(["count", "mode"], compact_fields)

        package = discover(
            snapshot,
            overlay=_overlay(),
            view="custom",
            command_ids=["mytools/do_thing"],
            contract_detail="package",
        )
        package_fields = package["selected"][0]["contract"]["result"]["fields"]
        self.assertEqual(1, package_fields[0]["minimum"])
        self.assertEqual(10, package_fields[0]["maximum"])
        self.assertTrue(package_fields[1]["allowedValuesIgnoreCase"])

    def test_agent_result_inventory_is_self_contained_for_all_schema_shapes(self):
        def scalar(kind, *, format_value="", nullable=False, enum_values=None):
            return {
                "kind": kind,
                "format": format_value,
                "nullable": nullable,
                "enumValues": enum_values or [],
                "fields": [],
            }

        object_schema = scalar("object")
        object_schema["fields"] = [
            {
                "name": "value",
                "schema": scalar("string"),
            }
        ]
        direct_cases = {
            "boolean": scalar("boolean"),
            "empty": scalar("empty"),
            "enum": scalar("enum", enum_values=["A", "B"]),
            "integer": scalar("integer", format_value="int32"),
            "number": scalar("number", format_value="float64"),
            "string": scalar("string", nullable=True),
            "object": object_schema,
            "array": {
                **scalar("array"),
                "items": scalar("string"),
            },
            "map": {
                **scalar("map"),
                "items": scalar("integer", format_value="int32"),
            },
        }
        for name, result_schema in direct_cases.items():
            with self.subTest(shape=name):
                snapshot = _snapshot_with_custom()
                snapshot["custom"]["commands"][0]["result"] = result_schema
                bundle = discover(
                    snapshot,
                    overlay=_overlay(),
                    view="custom",
                    command_ids=["mytools/do_thing"],
                )
                projected = bundle["selected"][0]["contract"]["result"]
                self.assertEqual(result_schema["kind"], projected["kind"])
                self.assertNotIn("$defs", projected)
                self.assertNotIn("$ref", projected)
                if name == "object":
                    self.assertEqual(["value"], projected["fields"])

        reference = {
            **scalar("reference"),
            "$ref": "d0",
            "$defs": {"d0": object_schema},
        }
        for container_kind in (None, "array", "map"):
            with self.subTest(reference_container=container_kind):
                snapshot = _snapshot_with_custom()
                if container_kind is None:
                    result_schema = reference
                    expected = {"kind": "object", "fields": ["value"]}
                else:
                    result_schema = {
                        **scalar(container_kind),
                        "items": {
                            **scalar("reference"),
                            "$ref": "d0",
                        },
                        "$defs": {"d0": object_schema},
                    }
                    expected = {
                        "kind": container_kind,
                        "items": {
                            "kind": "object",
                            "fields": ["value"],
                        },
                    }
                snapshot["custom"]["commands"][0]["result"] = result_schema
                bundle = discover(
                    snapshot,
                    overlay=_overlay(),
                    view="custom",
                    command_ids=["mytools/do_thing"],
                )
                projected = bundle["selected"][0]["contract"]["result"]
                self.assertEqual(expected, projected)
                encoded = json.dumps(projected)
                self.assertNotIn("$defs", encoded)
                self.assertNotIn("$ref", encoded)

        nullable_cases = (
            (False, True, False),
            (True, False, True),
        )
        for outer_nullable, target_nullable, expected_nullable in nullable_cases:
            with self.subTest(
                outer_nullable=outer_nullable,
                target_nullable=target_nullable,
            ):
                snapshot = _snapshot_with_custom()
                target = dict(object_schema)
                target["nullable"] = target_nullable
                snapshot["custom"]["commands"][0]["result"] = {
                    **scalar("reference", nullable=outer_nullable),
                    "$ref": "d0",
                    "$defs": {"d0": target},
                }
                bundle = discover(
                    snapshot,
                    overlay=_overlay(),
                    view="custom",
                    command_ids=["mytools/do_thing"],
                )
                projected = bundle["selected"][0]["contract"]["result"]
                self.assertEqual(
                    expected_nullable,
                    projected.get("nullable", False),
                )

        snapshot = _snapshot_with_custom()
        nullable_target = dict(object_schema)
        nullable_target["nullable"] = True
        snapshot["custom"]["commands"][0]["result"] = {
            **scalar("array"),
            "items": {
                **scalar("reference", nullable=False),
                "$ref": "d0",
            },
            "$defs": {"d0": nullable_target},
        }
        bundle = discover(
            snapshot,
            overlay=_overlay(),
            view="custom",
            command_ids=["mytools/do_thing"],
        )
        self.assertNotIn(
            "nullable",
            bundle["selected"][0]["contract"]["result"]["items"],
        )

        snapshot = _snapshot_with_custom()
        snapshot["custom"]["commands"][0]["result"] = {
            **scalar("reference"),
            "$ref": "d0",
            "$defs": {
                "d0": {
                    **scalar("array"),
                    "items": {
                        **scalar("reference"),
                        "$ref": "d0",
                    },
                }
            },
        }
        bundle = discover(
            snapshot,
            overlay=_overlay(),
            view="custom",
            command_ids=["mytools/do_thing"],
        )
        recursive_item = bundle["selected"][0]["contract"]["result"]["items"]
        self.assertEqual(
            {"kind": "reference", "opaque": True},
            recursive_item,
        )

    def test_custom_argument_projection_preserves_rich_schema_constraints(self):
        def scalar(kind, *, format_value="", nullable=False):
            return {
                "kind": kind,
                "format": format_value,
                "nullable": nullable,
                "enumValues": [],
                "fields": [],
            }

        score_field = {
            "name": "score",
            "schema": scalar("integer", format_value="int32"),
            "required": True,
            "minimum": 1,
            "maximum": 10,
            "allowedValues": ["1", "10"],
        }
        mode_field = {
            "name": "mode",
            "schema": scalar("string"),
            "required": True,
            "nonEmpty": True,
            "allowedValues": ['"Fast"', '"Safe"'],
            "allowedValuesIgnoreCase": True,
        }
        definition = scalar("object")
        definition["fields"] = [mode_field, score_field]
        rich_map_schema = {
            **scalar("map"),
            "items": {
                **scalar("reference"),
                "$ref": "d0",
            },
            "$defs": {"d0": definition},
        }
        snapshot = _snapshot_with_custom()
        command = snapshot["custom"]["commands"][0]
        command["arguments"] = [
            {
                "name": "count",
                "schema": scalar("integer", format_value="int32"),
                "required": False,
                "hasDefault": True,
                "defaultJson": "2",
                "nonEmpty": False,
                "hasMinimum": True,
                "minimum": 1,
                "hasMaximum": True,
                "maximum": 10,
                "allowedValues": ["1", "10", "2"],
                "allowedValuesIgnoreCase": False,
            },
            {
                "name": "mode",
                "schema": {
                    **scalar("enum"),
                    "enumValues": ["Fast", "Safe"],
                },
                "required": False,
                "hasDefault": True,
                "defaultJson": '"Fast"',
                "nonEmpty": False,
                "hasMinimum": False,
                "hasMaximum": False,
                "allowedValues": ['"Fast"', '"Safe"'],
                "allowedValuesIgnoreCase": True,
            },
            {
                "name": "payloads",
                "schema": rich_map_schema,
                "required": True,
                "hasDefault": False,
                "defaultJson": "",
                "nonEmpty": True,
                "hasMinimum": False,
                "hasMaximum": False,
                "allowedValues": [],
                "allowedValuesIgnoreCase": False,
            },
            {
                "name": "single",
                "schema": {
                    **scalar("reference", nullable=True),
                    "$ref": "d0",
                    "$defs": {"d0": definition},
                },
                "required": False,
                "hasDefault": True,
                "defaultJson": "null",
                "nonEmpty": False,
                "hasMinimum": False,
                "hasMaximum": False,
                "allowedValues": [],
                "allowedValuesIgnoreCase": False,
            },
        ]
        REGISTRY_PROTOCOL._validate_command(
            command,
            "custom",
            "custom command",
        )
        bundle = discover(
            snapshot,
            overlay=_overlay(),
            view="custom",
            command_ids=["mytools/do_thing"],
        )
        arguments = {
            argument["name"]: argument
            for argument in bundle["selected"][0]["contract"]["arguments"]
        }
        self.assertEqual(
            {
                "name": "payloads",
                "schema": {
                    "kind": "map",
                    "items": {"kind": "reference", "$ref": "d0"},
                    "$defs": {
                        "d0": {
                            "kind": "object",
                            "fields": [
                                {
                                    "name": "mode",
                                    "schema": {"kind": "string"},
                                    "required": True,
                                    "nonEmpty": True,
                                    "allowedValues": ['"Fast"', '"Safe"'],
                                    "allowedValuesIgnoreCase": True,
                                },
                                {
                                    "name": "score",
                                    "schema": {
                                        "kind": "integer",
                                        "format": "int32",
                                    },
                                    "required": True,
                                    "minimum": 1,
                                    "maximum": 10,
                                    "allowedValues": ["1", "10"],
                                },
                            ],
                        }
                    },
                },
                "required": True,
                "nonEmpty": True,
            },
            arguments["payloads"],
        )
        self.assertEqual(1, arguments["count"]["minimum"])
        self.assertEqual(10, arguments["count"]["maximum"])
        self.assertEqual("2", arguments["count"]["defaultJson"])
        self.assertEqual(["1", "10", "2"], arguments["count"]["allowedValues"])
        self.assertEqual(
            ["Fast", "Safe"],
            arguments["mode"]["schema"]["enumValues"],
        )
        self.assertTrue(arguments["mode"]["allowedValuesIgnoreCase"])
        self.assertEqual("reference", arguments["single"]["schema"]["kind"])
        self.assertEqual("d0", arguments["single"]["schema"]["$ref"])
        self.assertIn("$defs", arguments["single"]["schema"])

    def test_deny_policy_applies_to_project_defined_contract_ids(self):
        snapshot = _snapshot_with_custom()
        denied = snapshot["custom"]["commands"][0]
        denied["id"] = "editor/menu.open"
        denied["wire"] = {
            "commandNamespace": "editor",
            "action": "menu.open",
        }

        index = discover(snapshot, overlay=_overlay(), view="custom")
        self.assertEqual(0, index["totalCommands"])
        denied_result = discover(
            snapshot,
            overlay=_overlay(),
            view="custom",
            command_ids=["editor/menu.open"],
        )
        self.assertEqual([], denied_result["selected"])
        self.assertEqual(
            ["editor/menu.open"],
            [item["id"] for item in denied_result["denied"]],
        )

    def test_overlay_cannot_duplicate_package_owned_executable_fields(self):
        overlay = _overlay()
        overlay["commands"]["object/read"]["arguments"] = []
        with self.assertRaisesRegex(
            DiscoveryError,
            "duplicates executable fields",
        ):
            discover(_snapshot(), overlay=overlay)

    def test_agent_encoding_preserves_all_61_preflight_contracts(self):
        def expected_schema(schema):
            result = {"kind": schema["kind"]}
            if schema.get("format"):
                result["format"] = schema["format"]
            if schema.get("nullable"):
                result["nullable"] = True
            if "$ref" in schema:
                result["$ref"] = schema["$ref"]
            if "items" in schema:
                result["items"] = expected_schema(schema["items"])
            if schema.get("enumValues"):
                result["enumValues"] = schema["enumValues"]
            if schema.get("fields"):
                result["fields"] = [
                    expected_field(field)
                    for field in schema["fields"]
                ]
            if schema.get("$defs"):
                result["$defs"] = {
                    name: expected_schema(definition)
                    for name, definition in schema["$defs"].items()
                }
            return result

        def expected_field(field):
            result = {
                "name": field["name"],
                "schema": expected_schema(field["schema"]),
            }
            for source, target in (
                ("required", "required"),
                ("nonEmpty", "nonEmpty"),
                ("allowedValuesIgnoreCase", "allowedValuesIgnoreCase"),
            ):
                if field.get(source):
                    result[target] = True
            for name in ("minimum", "maximum"):
                if name in field:
                    result[name] = field[name]
            if field.get("allowedValues"):
                result["allowedValues"] = field["allowedValues"]
            return result

        def expected_argument(argument):
            result = {
                "name": argument["name"],
                "schema": expected_schema(argument["schema"]),
            }
            if argument.get("required"):
                result["required"] = True
            if argument.get("hasDefault"):
                result["defaultJson"] = argument["defaultJson"]
            if argument.get("nonEmpty"):
                result["nonEmpty"] = True
            if argument.get("hasMinimum"):
                result["minimum"] = argument["minimum"]
            if argument.get("hasMaximum"):
                result["maximum"] = argument["maximum"]
            if argument.get("allowedValues"):
                result["allowedValues"] = argument["allowedValues"]
            if argument.get("allowedValuesIgnoreCase"):
                result["allowedValuesIgnoreCase"] = True
            return result

        def expected_rule(rule):
            result = {"kind": rule["kind"]}
            for name in ("arguments", "requires"):
                if rule.get(name):
                    result[name] = rule[name]
            for name in ("whenArgument", "whenEqualsJson"):
                if rule.get(name):
                    result[name] = rule[name]
            return result

        snapshot = json.loads(
            (
                CLI_DIR
                / "local_fixtures"
                / "builtin_registry_snapshot.v1.json"
            ).read_text("utf-8")
        )
        authoring_index = discover(snapshot)
        domains = [item["id"] for item in authoring_index["domains"]]
        authoring_ids = [
            route["id"]
            for tier in ("core", "advanced")
            for route in discover(
                snapshot,
                domains=domains,
                tier=tier,
            )["routes"]
        ]
        control_ids = [
            route["id"]
            for route in discover(
                snapshot,
                view="control",
                domains=["control"],
            )["routes"]
        ]
        self.assertEqual(61, len(authoring_ids) + len(control_ids))

        for view, command_ids in (
            ("authoring", authoring_ids),
            ("control", control_ids),
        ):
            compact = discover(
                snapshot,
                view=view,
                command_ids=command_ids,
            )
            package = discover(
                snapshot,
                view=view,
                command_ids=command_ids,
                contract_detail="package",
            )
            compact_by_id = {
                item["contract"]["id"]: item["contract"]
                for item in compact["selected"]
            }
            package_by_id = {
                item["contract"]["id"]: item["contract"]
                for item in package["selected"]
            }
            self.assertEqual(set(package_by_id), set(compact_by_id))
            for command_id, source in package_by_id.items():
                with self.subTest(command_id=command_id):
                    projected = compact_by_id[command_id]
                    self.assertEqual(
                        {
                            name: True
                            for name, required in source[
                                "requirements"
                            ].items()
                            if required
                        },
                        projected["requirements"],
                    )
                    self.assertEqual(
                        [
                            expected_argument(argument)
                            for argument in source["arguments"]
                        ],
                        projected["arguments"],
                    )
                    self.assertEqual(
                        [
                            expected_rule(rule)
                            for rule in source["rules"]
                        ],
                        projected["rules"],
                    )
                    self.assertTrue(
                        {"wire", "summary", "partition"}.isdisjoint(projected)
                    )

    def test_real_overlay_exposes_exact_final_56_authoring_commands(self):
        snapshot = json.loads(
            (
                CLI_DIR
                / "local_fixtures"
                / "builtin_registry_snapshot.v1.json"
            ).read_text("utf-8")
        )
        index = discover(snapshot)
        self.assertEqual(56, index["totalCommands"])

        core = discover(
            snapshot,
            domains=[item["id"] for item in index["domains"]],
            tier="core",
        )
        advanced = discover(
            snapshot,
            domains=[item["id"] for item in index["domains"]],
            tier="advanced",
        )
        routes = core["routes"] + advanced["routes"]
        route_ids = [item["id"] for item in routes]

        self.assertEqual(56, len(route_ids))
        self.assertEqual(56, len(set(route_ids)))
        self.assertTrue(
            {
                "editor/menu.open",
                "editor/window.open",
                "editor/playmode.status",
                "transform/get",
                "command/list",
                "command/registry.fingerprint",
                "command/registry.snapshot",
                "session/list",
                "session/inspect",
                "session/reset",
            }.isdisjoint(route_ids)
        )
        advanced_ids = {item["id"] for item in advanced["routes"]}
        self.assertIn("asset/create_folder", advanced_ids)
        self.assertIn("screenshot/scene_view", advanced_ids)
        self.assertIn("project/asset.import", route_ids)
        self.assertIn("project/asset.reimport", route_ids)

        overlay = json.loads(
            (CLI_DIR / "routing_overlay.json").read_text("utf-8")
        )
        self.assertEqual(
            [],
            overlay["commands"]["prefab/unpack"]["verifyWith"],
        )
        self.assertEqual(
            [],
            overlay["commands"]["project/scene.open"]["verifyWith"],
        )
        self.assertEqual(
            [],
            overlay["commands"]["material/assign"]["verifyWith"],
        )
        self.assertTrue(
            all(
                item["fallbackPolicy"] == "prohibited"
                for item in overlay["denyPolicy"]
            )
        )

    def test_creation_under_known_parent_does_not_route_through_reparent(self):
        overlay = json.loads(
            (CLI_DIR / "routing_overlay.json").read_text("utf-8")
        )["commands"]
        create = overlay["gameobject/create"]
        reparent = overlay["gameobject/set_parent"]
        skill = (CLI_DIR.parents[1] / "SKILL.md").read_text("utf-8")
        normalized_skill = " ".join(skill.split())

        self.assertIn("known parent", create["selectWhen"])
        self.assertIn("existing", reparent["selectWhen"])
        self.assertIn("gameobject/create", reparent["avoidWhen"])
        self.assertIn("parentPath", reparent["avoidWhen"])
        self.assertIn(
            "create it at that parent directly",
            normalized_skill,
        )

    def test_contract_planning_defers_dynamic_drilldowns_and_respects_gaps(self):
        skill = (CLI_DIR.parents[1] / "SKILL.md").read_text("utf-8")
        normalized_skill = " ".join(skill.split())
        overlay = json.loads(
            (CLI_DIR / "routing_overlay.json").read_text("utf-8")
        )["commands"]

        self.assertIn(
            "Contract evidence is closed-world for task-specific signals",
            normalized_skill,
        )
        self.assertIn(
            "Do not exact-load a conditional drill-down",
            normalized_skill,
        )
        self.assertIn(
            "Runtime selector binding alone does not make a known",
            normalized_skill,
        )
        self.assertIn(
            "An explicit verifier limitation is authoritative",
            normalized_skill,
        )
        self.assertIn(
            "record a proof gap",
            normalized_skill,
        )
        for command_id in ("scene/hierarchy", "prefab/asset_hierarchy"):
            self.assertTrue(
                any(
                    "Missing Script" in limitation
                    for limitation in overlay[command_id]["limitations"]
                ),
                command_id,
            )

    def test_speculative_drilldowns_are_excluded_at_route_and_plan_levels(self):
        overlay = json.loads(
            (CLI_DIR / "routing_overlay.json").read_text("utf-8")
        )["commands"]
        for command_id in (
            "gameobject/get",
            "component/get",
            "prefab/asset_get",
            "prefab/asset_get_component",
        ):
            avoid_when = overlay[command_id]["avoidWhen"]
            self.assertIn(
                "upstream read returns a concrete target",
                avoid_when,
                command_id,
            )
            self.assertIn("Do not select or exact-load", avoid_when)

        for command_id in (
            "component/remove",
            "prefab/asset_remove_component",
        ):
            avoid_when = overlay[command_id]["avoidWhen"]
            self.assertIn(
                "Do not infer removal from a diagnostic scan",
                avoid_when,
                command_id,
            )
            self.assertIn("applicable repair rule", avoid_when, command_id)
            self.assertIn(
                "user explicitly requests removal",
                avoid_when,
                command_id,
            )

        skill = " ".join(
            (CLI_DIR.parents[1] / "SKILL.md").read_text("utf-8").split()
        )
        self.assertIn(
            "A possible later drill-down never widens the initial domain sets",
            skill,
        )
        self.assertIn(
            "Omit deferred drill-downs from both the exact selector set and "
            "the invocation plan",
            skill,
        )

    def test_scene_save_contract_exposes_new_scene_preconditions(self):
        snapshot = json.loads(
            (
                CLI_DIR
                / "local_fixtures"
                / "builtin_registry_snapshot.v1.json"
            ).read_text("utf-8")
        )
        bundle = discover(
            snapshot,
            command_ids=["project/scene.save"],
        )

        selected = bundle["selected"][0]
        scene_path = selected["contract"]["arguments"][0]
        self.assertEqual("scenePath", scene_path["name"])
        self.assertTrue(scene_path["required"])
        self.assertTrue(scene_path["nonEmpty"])
        expected_prepare = ["project/asset.list", "asset/create_folder"]
        self.assertEqual(
            expected_prepare,
            bundle["relations"][0]["prepareWith"],
        )
        self.assertEqual(
            ["project/scene.list"],
            bundle["relations"][0]["verifyWith"],
        )
        self.assertEqual(
            expected_prepare + ["project/scene.list"],
            [item["contract"]["id"] for item in bundle["related"]],
        )
        self.assertIn(
            "no built-in independent readback proves that an existing scene "
            "became clean",
            selected["limitations"][-1],
        )

    def test_route_cards_preserve_evidence_bound_verification(self):
        overlay = json.loads(
            (CLI_DIR / "routing_overlay.json").read_text("utf-8")
        )["commands"]

        instantiate = overlay["prefab/instantiate"]
        self.assertEqual(["gameobject/get"], instantiate["verifyWith"])
        instantiate_limit = " ".join(instantiate["limitations"])
        self.assertIn("returned instanceId", instantiate_limit)
        self.assertIn("never predict", instantiate_limit)
        self.assertIn("instance name or path", instantiate_limit)

        hierarchy_limit = " ".join(
            overlay["scene/hierarchy"]["limitations"]
        )
        self.assertIn("includeComponents=true", hierarchy_limit)
        self.assertIn(
            "explicit hierarchy-wide component scan",
            hierarchy_limit,
        )
        self.assertIn("otherwise keep it false", hierarchy_limit)

        material_get = overlay["material/get"]
        material_limit = " ".join(material_get["limitations"])
        self.assertIn("Renderer slot 0 only", material_limit)
        self.assertIn("higher slots", material_limit)
        self.assertIn("no built-in material-path proof", material_limit)


if __name__ == "__main__":
    unittest.main()
