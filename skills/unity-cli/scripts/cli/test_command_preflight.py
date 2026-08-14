"""Local-only TDD coverage for canonical-ID execution preflight."""

import copy
import json
import sys
import unittest
from pathlib import Path


CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CLI_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.command_preflight import (  # noqa: E402
    CommandPreflightError,
    prepare_batch,
    prepare_command,
)


def _snapshot():
    return json.loads(
        (
            CLI_DIR / "local_fixtures" / "builtin_registry_snapshot.v1.json"
        ).read_text("utf-8")
    )


def _custom_contract(
    snapshot,
    *,
    command_id,
    arguments,
    rules=(),
    requirements=None,
):
    contract = copy.deepcopy(
        next(
            item
            for item in snapshot["builtin"]["commands"]
            if item["id"] == "editor/status"
        )
    )
    contract.update(
        {
            "id": command_id,
            "wire": {
                "commandNamespace": "internal",
                "action": "execute",
            },
            "partition": "custom",
            "arguments": list(arguments),
            "rules": list(rules),
            "requirements": requirements
            or {
                "editor": False,
                "mainThread": False,
                "sessionId": False,
            },
        }
    )
    snapshot["custom"] = {
        "included": True,
        "count": 1,
        "fingerprint": "custom",
        "commands": [contract],
    }
    return contract


def _argument(name, schema, *, default=None, allowed_values=()):
    has_default = default is not None
    return {
        "name": name,
        "schema": schema,
        "required": not has_default,
        "hasDefault": has_default,
        "defaultJson": (
            json.dumps(default, separators=(",", ":"))
            if has_default
            else ""
        ),
        "nonEmpty": False,
        "hasMinimum": False,
        "hasMaximum": False,
        "allowedValues": list(allowed_values),
        "allowedValuesIgnoreCase": False,
    }


def _scalar_schema(kind, format_name=""):
    return {
        "kind": kind,
        "format": format_name,
        "nullable": False,
        "enumValues": [],
        "fields": [],
    }


class CanonicalCommandPreflightTests(unittest.TestCase):
    def test_canonical_id_resolves_package_wire_and_normalizes_argument_names(self):
        prepared = prepare_command(
            _snapshot(),
            "project/scene.open",
            {
                "SCENEPATH": "Assets/Scenes/Main.unity",
                "MODE": "ADDITIVE",
            },
        )

        self.assertEqual("project/scene.open", prepared["id"])
        self.assertEqual(
            {
                "commandNamespace": "project",
                "action": "scene.open",
            },
            prepared["wire"],
        )
        self.assertEqual(
            {
                "scenePath": "Assets/Scenes/Main.unity",
                "mode": "ADDITIVE",
            },
            prepared["args"],
        )

    def test_argument_name_matching_is_fail_closed_for_unicode_casefold(self):
        snapshot = _snapshot()
        _custom_contract(
            snapshot,
            command_id="custom/unicode-name",
            arguments=[
                _argument(
                    "straße",
                    _scalar_schema("string"),
                )
            ],
        )
        with self.assertRaisesRegex(
            CommandPreflightError,
            "unknown argument",
        ):
            prepare_command(
                snapshot,
                "custom/unicode-name",
                {"STRASSE": "value"},
            )

    def test_unknown_id_argument_and_invalid_scalar_fail_closed(self):
        with self.assertRaisesRegex(CommandPreflightError, "unknown command id"):
            prepare_command(_snapshot(), "missing/command", {})
        with self.assertRaisesRegex(CommandPreflightError, "unknown argument"):
            prepare_command(
                _snapshot(),
                "gameobject/get",
                {"paht": "Player"},
            )
        with self.assertRaisesRegex(CommandPreflightError, "primitiveType"):
            prepare_command(
                _snapshot(),
                "gameobject/create",
                {"primitiveType": "Triangle"},
            )
        with self.assertRaisesRegex(CommandPreflightError, "index"):
            prepare_command(
                _snapshot(),
                "prefab/asset_get_component",
                {
                    "assetPath": "Assets/Thing.prefab",
                    "typeName": "BoxCollider",
                    "index": -1,
                },
            )
        with self.assertRaisesRegex(
            CommandPreflightError,
            "float32|finite|range",
        ):
            prepare_command(
                _snapshot(),
                "transform/set",
                {
                    "path": "Player",
                    "position": {
                        "x": 10**400,
                        "y": 0,
                        "z": 0,
                    },
                },
            )

    def test_nested_schema_and_required_fields_are_validated(self):
        with self.assertRaisesRegex(CommandPreflightError, "position.*missing"):
            prepare_command(
                _snapshot(),
                "transform/set",
                {
                    "path": "Player",
                    "position": {"x": 1, "y": 2},
                },
            )
        with self.assertRaisesRegex(CommandPreflightError, "requires.*assetPath"):
            prepare_command(
                _snapshot(),
                "prefab/asset_get_component",
                {"typeName": "BoxCollider"},
            )

    def test_nested_reference_field_constraints_share_root_definitions(self):
        snapshot = _snapshot()
        schema = {
            "kind": "object",
            "format": "",
            "nullable": False,
            "enumValues": [],
            "fields": [
                {
                    "name": "code",
                    "schema": {
                        "kind": "reference",
                        "format": "",
                        "nullable": False,
                        "$ref": "d0",
                        "enumValues": [],
                        "fields": [],
                    },
                    "required": True,
                    "allowedValues": ['"ok"'],
                }
            ],
            "$defs": {
                "d0": _scalar_schema("string"),
            },
        }
        _custom_contract(
            snapshot,
            command_id="custom/reference",
            arguments=[_argument("payload", schema)],
        )
        prepared = prepare_command(
            snapshot,
            "custom/reference",
            {"payload": {"code": "ok"}},
        )
        self.assertEqual({"payload": {"code": "ok"}}, prepared["args"])
        with self.assertRaisesRegex(CommandPreflightError, "must be one of"):
            prepare_command(
                snapshot,
                "custom/reference",
                {"payload": {"code": "bad"}},
            )

    def test_selector_mutation_and_conditional_rules_match_package_contract(self):
        for invalid in (
            {},
            {"path": "Player", "instanceId": 10},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    CommandPreflightError,
                    "exactly one",
                ):
                    prepare_command(
                        _snapshot(),
                        "gameobject/get",
                        invalid,
                    )

        with self.assertRaisesRegex(
            CommandPreflightError,
            "at least one.*mutation",
        ):
            prepare_command(
                _snapshot(),
                "transform/set",
                {"path": "Player"},
            )
        with self.assertRaisesRegex(
            CommandPreflightError,
            "requires.*scenePath",
        ):
            prepare_command(
                _snapshot(),
                "project/scene.save",
                {"saveAsCopy": True},
            )
        with self.assertRaisesRegex(
            CommandPreflightError,
            "at least one",
        ):
            prepare_command(
                _snapshot(),
                "asset/delete",
                {},
            )

    def test_explicit_session_requirement_is_preflighted(self):
        with self.assertRaisesRegex(
            CommandPreflightError,
            "explicit --session",
        ):
            prepare_command(
                _snapshot(),
                "session/inspect",
                {},
            )
        prepared = prepare_command(
            _snapshot(),
            "session/inspect",
            {},
            session_id="agent-session",
        )
        self.assertEqual("session/inspect", prepared["id"])

    def test_editor_requirement_is_preflighted_against_execution_mode(self):
        with self.assertRaisesRegex(
            CommandPreflightError,
            "requires editor mode",
        ):
            prepare_command(
                _snapshot(),
                "editor/status",
                {},
                mode="runtime",
            )

    def test_custom_contract_passes_through_but_deny_policy_still_wins(self):
        snapshot = _snapshot()
        custom = copy.deepcopy(
            next(
                item
                for item in snapshot["builtin"]["commands"]
                if item["id"] == "editor/status"
            )
        )
        custom["id"] = "mytools/do_thing"
        custom["wire"] = {
            "commandNamespace": "mytools",
            "action": "do_thing",
        }
        custom["partition"] = "custom"
        snapshot["custom"] = {
            "included": True,
            "count": 1,
            "fingerprint": "custom",
            "commands": [custom],
        }
        prepared = prepare_command(
            snapshot,
            "mytools/do_thing",
            {
                argument["name"]: json.loads(argument["defaultJson"])
                for argument in custom["arguments"]
                if argument["hasDefault"]
            },
        )
        self.assertEqual("custom", prepared["partition"])

        custom["id"] = "editor/menu.open"
        custom["wire"] = {
            "commandNamespace": "editor",
            "action": "menu.open",
        }
        with self.assertRaisesRegex(
            CommandPreflightError,
            "fallback through snippets or raw exec is prohibited",
        ):
            prepare_command(snapshot, "editor/menu.open", {})

    def test_mutation_presence_counts_explicit_false_and_zero(self):
        for value, schema in (
            (False, _scalar_schema("boolean")),
            (0, _scalar_schema("integer", "int32")),
        ):
            with self.subTest(value=value):
                snapshot = _snapshot()
                _custom_contract(
                    snapshot,
                    command_id="custom/mutate",
                    arguments=[
                        _argument("value", schema, default=value),
                    ],
                    rules=[
                        {
                            "kind": "atLeastOneMutation",
                            "arguments": ["value"],
                            "whenArgument": "",
                            "whenEqualsJson": "",
                            "requires": [],
                        }
                    ],
                )
                prepared = prepare_command(
                    snapshot,
                    "custom/mutate",
                    {"value": value},
                )
                self.assertEqual({"value": value}, prepared["args"])

    def test_empty_dto_object_counts_as_a_supplied_mutation(self):
        snapshot = _snapshot()
        _custom_contract(
            snapshot,
            command_id="custom/object-mutate",
            arguments=[
                _argument(
                    "value",
                    {
                        "kind": "object",
                        "format": "",
                        "nullable": False,
                        "enumValues": [],
                        "fields": [],
                    },
                )
            ],
            rules=[
                {
                    "kind": "atLeastOneMutation",
                    "arguments": ["value"],
                    "whenArgument": "",
                    "whenEqualsJson": "",
                    "requires": [],
                }
            ],
        )
        prepared = prepare_command(
            snapshot,
            "custom/object-mutate",
            {"value": {}},
        )
        self.assertEqual({"value": {}}, prepared["args"])

    def test_float32_allowed_values_and_defaults_use_bound_value_semantics(self):
        snapshot = _snapshot()
        argument = _argument(
            "value",
            _scalar_schema("number", "float32"),
            default=0.1,
            allowed_values=("0.1",),
        )
        _custom_contract(
            snapshot,
            command_id="custom/float",
            arguments=[argument],
            rules=[
                {
                    "kind": "atLeastOneOf",
                    "arguments": ["value"],
                    "whenArgument": "",
                    "whenEqualsJson": "",
                    "requires": [],
                }
            ],
        )
        with self.assertRaisesRegex(
            CommandPreflightError,
            "at least one",
        ):
            prepare_command(
                snapshot,
                "custom/float",
                {"value": 0.1},
            )

        snapshot["custom"]["commands"][0]["rules"] = []
        prepared = prepare_command(
            snapshot,
            "custom/float",
            {"value": 0.1},
        )
        self.assertEqual({"value": 0.1}, prepared["args"])

    def test_top_level_numeric_bounds_use_package_bound_float32_value(self):
        snapshot = _snapshot()
        argument = _argument(
            "value",
            _scalar_schema("number", "float32"),
        )
        argument["hasMinimum"] = True
        argument["minimum"] = 0.1
        _custom_contract(
            snapshot,
            command_id="custom/float-bound",
            arguments=[argument],
        )
        prepared = prepare_command(
            snapshot,
            "custom/float-bound",
            {"value": 0.099999999},
        )
        self.assertEqual(
            {"value": 0.099999999},
            prepared["args"],
        )

    def test_char_uses_utf16_length_and_ignore_case_is_fail_closed(self):
        snapshot = _snapshot()
        _custom_contract(
            snapshot,
            command_id="custom/char",
            arguments=[
                _argument(
                    "value",
                    _scalar_schema("string", "char"),
                )
            ],
        )
        with self.assertRaisesRegex(
            CommandPreflightError,
            "one character",
        ):
            prepare_command(
                snapshot,
                "custom/char",
                {"value": "😀"},
            )

        prepared = prepare_command(
            snapshot,
            "custom/char",
            {"value": "\ud800"},
        )
        self.assertEqual(
            {"value": "\ud800"},
            prepared["args"],
        )

        snapshot = _snapshot()
        argument = _argument(
            "value",
            _scalar_schema("string"),
            allowed_values=('"straße"',),
        )
        argument["allowedValuesIgnoreCase"] = True
        _custom_contract(
            snapshot,
            command_id="custom/ordinal-case",
            arguments=[argument],
        )
        with self.assertRaisesRegex(
            CommandPreflightError,
            "must be one of",
        ):
            prepare_command(
                snapshot,
                "custom/ordinal-case",
                {"value": "STRASSE"},
            )

    def test_map_input_schema_fails_closed_like_package_dispatch(self):
        snapshot = _snapshot()
        _custom_contract(
            snapshot,
            command_id="custom/map",
            arguments=[
                _argument(
                    "values",
                    {
                        "kind": "map",
                        "format": "",
                        "nullable": False,
                        "items": _scalar_schema("string"),
                        "enumValues": [],
                        "fields": [],
                    },
                )
            ],
        )
        with self.assertRaisesRegex(
            CommandPreflightError,
            "map.*unsupported",
        ):
            prepare_command(
                snapshot,
                "custom/map",
                {"values": {"one": "value"}},
            )

    def test_batch_prepares_every_item_or_fails_with_index(self):
        prepared = prepare_batch(
            _snapshot(),
            [
                {
                    "id": "gameobject/get",
                    "args": {"path": "Player"},
                },
                {
                    "id": "project/scene.save",
                    "args": {
                        "scenePath": "Assets/Scenes/TrainingRoom.unity",
                    },
                },
            ],
        )
        self.assertEqual(
            ["gameobject/get", "project/scene.save"],
            [item["id"] for item in prepared],
        )

        with self.assertRaisesRegex(
            CommandPreflightError,
            "batch command 1.*requires argument scenePath",
        ):
            prepare_batch(
                _snapshot(),
                [
                    {
                        "id": "gameobject/get",
                        "args": {"path": "Player"},
                    },
                    {
                        "id": "project/scene.save",
                        "args": {},
                    },
                ],
            )

        with self.assertRaisesRegex(
            CommandPreflightError,
            "scenePath.*must not be empty",
        ):
            prepare_command(
                _snapshot(),
                "project/scene.save",
                {"scenePath": ""},
            )

        with self.assertRaisesRegex(
            CommandPreflightError,
            "batch command 1.*unknown argument",
        ):
            prepare_batch(
                _snapshot(),
                [
                    {
                        "id": "gameobject/get",
                        "args": {"path": "Player"},
                    },
                    {
                        "id": "project/scene.save",
                        "args": {"path": "wrong"},
                    },
                ],
            )

    def test_batch_requires_nonempty_exact_id_and_args_items(self):
        with self.assertRaisesRegex(
            CommandPreflightError,
            "non-empty array",
        ):
            prepare_batch(_snapshot(), [])
        with self.assertRaisesRegex(
            CommandPreflightError,
            "unknown field.*action",
        ):
            prepare_batch(
                _snapshot(),
                [
                    {
                        "id": "editor/status",
                        "args": {},
                        "action": "status",
                    }
                ],
            )
        with self.assertRaisesRegex(
            CommandPreflightError,
            "needs exactly.*id.*args",
        ):
            prepare_batch(
                _snapshot(),
                [{"id": "editor/status"}],
            )


if __name__ == "__main__":
    unittest.main()
