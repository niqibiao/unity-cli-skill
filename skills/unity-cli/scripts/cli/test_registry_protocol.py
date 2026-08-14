"""Local-only cross-language checks for registry schema v1 canonical bytes."""

import copy
import json
import sys
import unittest
from pathlib import Path


CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CLI_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.registry_protocol import (  # noqa: E402
    RegistryProtocolError,
    compute_partition_fingerprint,
    compute_registry_generation,
    validate_fingerprint,
    validate_snapshot,
    validate_unchanged_response,
)


FIXTURE = CLI_DIR / "local_fixtures" / "builtin_registry_snapshot.v1.json"


class RegistryProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = json.loads(FIXTURE.read_text("utf-8"))

    def test_local_fixture_is_a_valid_builtin_snapshot(self):
        validated = validate_snapshot(
            self.snapshot,
            required_included="builtin",
        )

        self.assertEqual(
            validated["builtin"]["count"],
            len(validated["builtin"]["commands"]),
        )
        self.assertGreater(validated["builtin"]["count"], 0)
        self.assertFalse(validated["custom"]["included"])

    def test_canonical_writer_agrees_with_package_serializer(self):
        # The runtime never recomputes fingerprints; this test is the
        # cross-implementation gate that keeps the Python canonical writer
        # byte-compatible with the package's BinaryWriter layout.
        computed = compute_partition_fingerprint(
            "builtin",
            self.snapshot["builtin"]["commands"],
        )
        self.assertEqual(self.snapshot["builtin"]["fingerprint"], computed)

        generation = compute_registry_generation(
            self.snapshot["builtin"]["count"],
            self.snapshot["builtin"]["fingerprint"],
            self.snapshot["custom"]["count"],
            self.snapshot["custom"]["fingerprint"],
        )
        self.assertEqual(self.snapshot["registryGeneration"], generation)

        tampered = copy.deepcopy(self.snapshot["builtin"]["commands"])
        tampered[0]["summary"] = "tampered"
        self.assertNotEqual(
            computed,
            compute_partition_fingerprint("builtin", tampered),
        )

    def test_bool_is_not_accepted_as_integer_or_boolean_substitute(self):
        tampered = copy.deepcopy(self.snapshot)
        tampered["builtin"]["commands"][0]["requirements"]["editor"] = 1

        with self.assertRaises(RegistryProtocolError):
            validate_snapshot(tampered, required_included="builtin")

    def test_non_ordinal_command_order_is_rejected(self):
        tampered = copy.deepcopy(self.snapshot)
        commands = tampered["builtin"]["commands"]
        commands[0], commands[1] = commands[1], commands[0]

        with self.assertRaisesRegex(RegistryProtocolError, "ordinal"):
            validate_snapshot(tampered, required_included="builtin")

    def test_enum_schema_is_valid_for_custom_contracts(self):
        command = copy.deepcopy(self.snapshot["builtin"]["commands"][0])
        command["id"] = "studio/mode"
        command["wire"] = {
            "commandNamespace": "studio",
            "action": "mode",
        }
        command["partition"] = "custom"
        enum_schema = command["arguments"][0]["schema"]
        enum_schema["kind"] = "enum"
        enum_schema["format"] = "StudioMode"
        enum_schema["enumValues"] = ["Fast", "Safe"]

        fingerprint = compute_partition_fingerprint("custom", [command])

        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")

    def test_exponent_overflow_is_rejected_in_nested_default_json(self):
        command = copy.deepcopy(
            next(
                item
                for item in self.snapshot["builtin"]["commands"]
                if item["id"] == "asset/delete"
            )
        )
        command["id"] = "studio/delete"
        command["wire"] = {
            "commandNamespace": "studio",
            "action": "delete",
        }
        command["partition"] = "custom"
        command["arguments"][0]["defaultJson"] = '{"nested":[1e400]}'

        with self.assertRaisesRegex(RegistryProtocolError, "finite valid JSON"):
            compute_partition_fingerprint("custom", [command])

    def test_validate_fingerprint_accepts_only_bare_digests(self):
        validate_fingerprint(self.snapshot["builtin"]["fingerprint"])

        with self.assertRaises(RegistryProtocolError):
            validate_fingerprint({"registryGeneration": "0" * 64})
        with self.assertRaises(RegistryProtocolError):
            validate_fingerprint("NOT-A-DIGEST")

    def test_unchanged_answer_shape_is_enforced(self):
        answer = {
            "schemaVersion": 1,
            "registryGeneration": self.snapshot["registryGeneration"],
            "unchanged": True,
        }
        self.assertEqual(answer, validate_unchanged_response(answer))

        with self.assertRaises(RegistryProtocolError):
            validate_unchanged_response({**answer, "unchanged": False})
        with self.assertRaises(RegistryProtocolError):
            validate_unchanged_response({**answer, "registryGeneration": ""})
        with self.assertRaises(RegistryProtocolError):
            validate_unchanged_response({**answer, "builtin": {}})


if __name__ == "__main__":
    unittest.main()
