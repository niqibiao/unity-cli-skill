"""Canonical-ID command preflight against package-owned registry contracts."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any, Mapping, Sequence


_OVERLAY_PATH = Path(__file__).with_name("routing_overlay.json")
_INTEGER_RANGES = {
    "int8": (-(1 << 7), (1 << 7) - 1),
    "uint8": (0, (1 << 8) - 1),
    "int16": (-(1 << 15), (1 << 15) - 1),
    "uint16": (0, (1 << 16) - 1),
    "int32": (-(1 << 31), (1 << 31) - 1),
    "uint32": (0, (1 << 32) - 1),
    "int64": (-(1 << 63), (1 << 63) - 1),
    "uint64": (0, (1 << 64) - 1),
    "": (-(1 << 63), (1 << 63) - 1),
}
_DECIMAL_UNSUPPORTED = (
    "uses the decimal wire format, which this CLI does not support"
)


class CommandPreflightError(ValueError):
    """A canonical command request violates its resolved package contract."""


def _load_deny_policy(path: Path = _OVERLAY_PATH) -> dict[str, Mapping[str, Any]]:
    try:
        overlay = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommandPreflightError(
            f"unable to load command deny policy: {exc}"
        ) from exc
    raw_policies = overlay.get("denyPolicy")
    if not isinstance(raw_policies, list):
        raise CommandPreflightError("routing overlay denyPolicy must be a list")
    policies: dict[str, Mapping[str, Any]] = {}
    for raw_policy in raw_policies:
        if not isinstance(raw_policy, dict):
            raise CommandPreflightError("every deny policy must be an object")
        command_id = raw_policy.get("id")
        if not isinstance(command_id, str) or not command_id:
            raise CommandPreflightError("every deny policy needs a command id")
        if raw_policy.get("fallbackPolicy") != "prohibited":
            raise CommandPreflightError(
                f"deny policy {command_id} must prohibit fallback"
            )
        reason = raw_policy.get("reason")
        if not isinstance(reason, str) or not reason:
            raise CommandPreflightError(
                f"deny policy {command_id} needs a reason"
            )
        if command_id in policies:
            raise CommandPreflightError(
                f"duplicate deny policy for {command_id}"
            )
        policies[command_id] = raw_policy
    return policies


def _contracts_by_id(
    snapshot: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(snapshot, Mapping):
        raise CommandPreflightError("registry snapshot must be an object")
    contracts: dict[str, Mapping[str, Any]] = {}
    for partition_name in ("builtin", "custom"):
        partition = snapshot.get(partition_name)
        if not isinstance(partition, Mapping):
            raise CommandPreflightError(
                f"registry snapshot is missing {partition_name} partition"
            )
        commands = partition.get("commands")
        if not isinstance(commands, list):
            raise CommandPreflightError(
                f"registry {partition_name} commands must be an array"
            )
        if not partition.get("included"):
            if commands:
                raise CommandPreflightError(
                    f"registry {partition_name} is excluded but has commands"
                )
            continue
        for contract in commands:
            if not isinstance(contract, Mapping):
                raise CommandPreflightError(
                    f"registry {partition_name} contract must be an object"
                )
            command_id = contract.get("id")
            if not isinstance(command_id, str) or not command_id:
                raise CommandPreflightError(
                    f"registry {partition_name} contract needs an id"
                )
            if command_id in contracts:
                raise CommandPreflightError(
                    f"duplicate command id in registry: {command_id}"
                )
            contracts[command_id] = contract
    return contracts


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _ordinal_ignore_case_equal(left: str, right: str) -> bool:
    """Conservative subset of .NET OrdinalIgnoreCase.

    ASCII case pairs are exact. Non-ASCII values compare only when already
    identical so Python's broader Unicode case folding cannot admit a value the
    package would reject.
    """
    if left == right:
        return True
    if not left.isascii() or not right.isascii():
        return False
    return left.lower() == right.lower()


def _normalize_contract_value(
    value: Any,
    schema: Mapping[str, Any],
    definitions: Mapping[str, Any] | None = None,
) -> Any:
    if definitions is None:
        definitions = schema.get("$defs", {})
    if value is None:
        return None
    kind = schema.get("kind")
    if kind == "reference":
        target = definitions.get(schema.get("$ref"))
        if target is None:
            raise CommandPreflightError(
                f"unresolved comparison schema reference {schema.get('$ref')!r}"
            )
        return _normalize_contract_value(value, target, definitions)
    if kind == "array" and isinstance(value, list):
        return [
            _normalize_contract_value(item, schema["items"], definitions)
            for item in value
        ]
    if kind == "number":
        format_name = schema.get("format", "")
        if format_name == "float32":
            return struct.unpack("!f", struct.pack("!f", value))[0]
        if format_name == "decimal":
            raise CommandPreflightError(_DECIMAL_UNSUPPORTED)
        return float(value)
    return value


def _normalize_wire_value(
    value: Any,
    schema: Mapping[str, Any],
    definitions: Mapping[str, Any] | None = None,
) -> Any:
    """Retain contract decimals while preserving prior float wire semantics."""

    if definitions is None:
        definitions = schema.get("$defs", {})
    if value is None:
        return None
    kind = schema.get("kind")
    if kind == "reference":
        target = definitions.get(schema.get("$ref"))
        if target is None:
            raise CommandPreflightError(
                f"unresolved wire schema reference {schema.get('$ref')!r}"
            )
        return _normalize_wire_value(value, target, definitions)
    if kind == "array" and isinstance(value, list):
        return [
            _normalize_wire_value(item, schema["items"], definitions)
            for item in value
        ]
    if kind == "object" and isinstance(value, dict):
        fields = {
            field["name"]: field["schema"]
            for field in schema.get("fields", [])
        }
        return {
            name: _normalize_wire_value(item, fields[name], definitions)
            for name, item in value.items()
        }
    if kind == "number" and schema.get("format", "") == "decimal":
        raise CommandPreflightError(_DECIMAL_UNSUPPORTED)
    return value


def _matches_encoded_value(
    value: Any,
    encoded: str,
    schema: Mapping[str, Any],
    *,
    ignore_case: bool = False,
    definitions: Mapping[str, Any] | None = None,
) -> bool:
    try:
        expected = json.loads(encoded)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CommandPreflightError(
            f"contract contains invalid encoded value {encoded!r}"
        ) from exc
    actual = _normalize_contract_value(value, schema, definitions)
    expected = _normalize_contract_value(expected, schema, definitions)
    if ignore_case and isinstance(actual, str) and isinstance(expected, str):
        return _ordinal_ignore_case_equal(actual, expected)
    return actual == expected


def _fail(path: str, message: str) -> None:
    raise CommandPreflightError(f"{path}: {message}")


def _validate_integer(value: Any, schema: Mapping[str, Any], path: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(path, "must be an integer")
    value_range = _INTEGER_RANGES.get(schema.get("format", ""))
    if value_range is None:
        _fail(path, f"uses unsupported integer format {schema.get('format')!r}")
    minimum, maximum = value_range
    if value < minimum or value > maximum:
        _fail(path, f"is out of range for {schema.get('format') or 'int64'}")


def _validate_number(value: Any, schema: Mapping[str, Any], path: str) -> None:
    if not _is_number(value):
        _fail(path, "must be a finite number")
    format_name = schema.get("format", "")
    if format_name == "float32":
        try:
            packed = struct.pack("!f", value)
            unpacked = struct.unpack("!f", packed)[0]
        except (OverflowError, struct.error):
            _fail(path, "is out of range for float32")
        if not math.isfinite(unpacked):
            _fail(path, "is out of range for float32")
    elif format_name == "decimal":
        _fail(path, _DECIMAL_UNSUPPORTED)
    elif format_name in ("", "float64"):
        try:
            double_value = float(value)
        except (OverflowError, ValueError):
            _fail(path, "is out of range for float64")
        if not math.isfinite(double_value):
            _fail(path, "is out of range for float64")
    else:
        _fail(path, f"uses unsupported number format {format_name!r}")


def _validate_constraints(
    value: Any,
    descriptor: Mapping[str, Any],
    path: str,
    definitions: Mapping[str, Any] | None = None,
) -> None:
    if value is None:
        return
    if descriptor.get("nonEmpty"):
        if (
            isinstance(value, str)
            and not value.strip()
        ) or (
            isinstance(value, list)
            and not value
        ):
            _fail(path, "must not be empty")
    numeric_value = None
    if "minimum" in descriptor or "maximum" in descriptor:
        if not _is_number(value):
            _fail(path, "must be numeric")
        try:
            numeric_value = float(value)
        except (OverflowError, ValueError):
            _fail(path, "must be numeric")
        if not math.isfinite(numeric_value):
            _fail(path, "must be numeric")
    if "minimum" in descriptor:
        if numeric_value < descriptor["minimum"]:
            _fail(
                path,
                f"must be greater than or equal to {descriptor['minimum']}",
            )
    if "maximum" in descriptor:
        if numeric_value > descriptor["maximum"]:
            _fail(
                path,
                f"must be less than or equal to {descriptor['maximum']}",
            )
    allowed_values = descriptor.get("allowedValues", [])
    if allowed_values:
        schema = descriptor.get("schema")
        if not isinstance(schema, Mapping):
            _fail(path, "contract is missing its comparison schema")
        matched = any(
            _matches_encoded_value(
                value,
                allowed,
                schema,
                ignore_case=descriptor.get(
                    "allowedValuesIgnoreCase",
                    False,
                ),
                definitions=definitions,
            )
            for allowed in allowed_values
        )
        if not matched:
            _fail(
                path,
                "must be one of: " + ", ".join(allowed_values),
            )


def _validate_schema(
    value: Any,
    schema: Mapping[str, Any],
    path: str,
    definitions: Mapping[str, Any] | None = None,
) -> None:
    if definitions is None:
        definitions = schema.get("$defs", {})
    if value is None:
        if schema.get("nullable"):
            return
        _fail(path, "null is not allowed")

    kind = schema.get("kind")
    if kind == "reference":
        reference = schema.get("$ref")
        target = definitions.get(reference)
        if target is None:
            _fail(path, f"has unresolved schema reference {reference!r}")
        _validate_schema(value, target, path, definitions)
        return
    if kind == "empty":
        return
    if kind == "string":
        if not isinstance(value, str):
            _fail(path, "must be a string")
        if schema.get("format") == "char":
            utf16_units = len(
                value.encode("utf-16-le", errors="surrogatepass")
            ) // 2
            if utf16_units != 1:
                _fail(path, "must contain exactly one character")
        return
    if kind == "boolean":
        if not isinstance(value, bool):
            _fail(path, "must be a boolean")
        return
    if kind == "enum":
        if not isinstance(value, str) or value not in schema.get(
            "enumValues",
            [],
        ):
            _fail(
                path,
                "must be one of: " + ", ".join(schema.get("enumValues", [])),
            )
        return
    if kind == "integer":
        _validate_integer(value, schema, path)
        return
    if kind == "number":
        _validate_number(value, schema, path)
        return
    if kind == "array":
        if not isinstance(value, list):
            _fail(path, "must be an array")
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            _validate_schema(
                item,
                item_schema,
                f"{path}[{index}]",
                definitions,
            )
        return
    if kind == "map":
        _fail(path, "map input schemas are unsupported by package dispatch")
    if kind == "object":
        if not isinstance(value, dict):
            _fail(path, "must be an object")
        fields = {
            field["name"]: field
            for field in schema.get("fields", [])
        }
        unknown = sorted(set(value) - set(fields))
        if unknown:
            _fail(path, "has unknown field(s): " + ", ".join(unknown))
        for field_name, field in fields.items():
            field_path = f"{path}.{field_name}"
            if field.get("required") and field_name not in value:
                _fail(path, f"is missing required field {field_name}")
            if field_name not in value:
                continue
            field_value = value[field_name]
            _validate_schema(
                field_value,
                field["schema"],
                field_path,
                definitions,
            )
            _validate_constraints(
                field_value,
                field,
                field_path,
                definitions,
            )
        return
    _fail(path, f"uses unsupported schema kind {kind!r}")


def _meaningfully_present(
    name: str,
    supplied: set[str],
    bound_values: Mapping[str, Any],
    arguments: Mapping[str, Mapping[str, Any]],
) -> bool:
    if name not in supplied or name not in bound_values:
        return False
    value = bound_values[name]
    if value is None or value == "":
        return False
    if isinstance(value, list) and not value:
        return False
    descriptor = arguments.get(name)
    if descriptor is None or not descriptor.get("hasDefault"):
        return True
    if descriptor.get("defaultJson") == "null":
        return True
    return not _matches_encoded_value(
        value,
        descriptor.get("defaultJson", ""),
        descriptor["schema"],
    )


def _supplied_nonempty(
    name: str,
    supplied: set[str],
    bound_values: Mapping[str, Any],
) -> bool:
    if name not in supplied or name not in bound_values:
        return False
    value = bound_values[name]
    if value is None or value == "":
        return False
    return not isinstance(value, list) or bool(value)


def _validate_rules(
    command_id: str,
    rules: Sequence[Mapping[str, Any]],
    supplied: set[str],
    bound_values: Mapping[str, Any],
    arguments: Mapping[str, Mapping[str, Any]],
) -> None:
    for rule in rules:
        kind = rule.get("kind")
        rule_arguments = list(rule.get("arguments", []))
        presence = (
            lambda name: _supplied_nonempty(
                name,
                supplied,
                bound_values,
            )
            if kind == "atLeastOneMutation"
            else _meaningfully_present(
                name,
                supplied,
                bound_values,
                arguments,
            )
        )
        present_count = sum(
            1
            for name in rule_arguments
            if presence(name)
        )
        rendered = ", ".join(rule_arguments)
        if kind == "exactlyOneOf" and present_count != 1:
            raise CommandPreflightError(
                f"{command_id} needs exactly one of [{rendered}]"
            )
        if kind == "atMostOneOf" and present_count > 1:
            raise CommandPreflightError(
                f"{command_id} accepts at most one of [{rendered}]"
            )
        if kind in ("atLeastOneOf", "atLeastOneMutation") and present_count < 1:
            suffix = " mutation field" if kind == "atLeastOneMutation" else ""
            raise CommandPreflightError(
                f"{command_id} needs at least one{suffix} of [{rendered}]"
            )
        if kind != "requiresWhen":
            continue
        when_name = rule.get("whenArgument", "")
        if when_name not in bound_values:
            condition = False
        elif rule.get("whenEqualsJson"):
            descriptor = arguments.get(when_name)
            condition = (
                descriptor is not None
                and _matches_encoded_value(
                    bound_values[when_name],
                    rule["whenEqualsJson"],
                    descriptor["schema"],
                )
            )
        else:
            condition = _meaningfully_present(
                when_name,
                supplied,
                bound_values,
                arguments,
            )
        if not condition:
            continue
        missing = [
            name
            for name in rule.get("requires", [])
            if not _supplied_nonempty(name, supplied, bound_values)
        ]
        if missing:
            raise CommandPreflightError(
                f"{command_id} requires {', '.join(missing)} when "
                f"{when_name} matches {rule.get('whenEqualsJson')}"
            )


def prepare_command(
    snapshot: Mapping[str, Any],
    command_id: str,
    args: Mapping[str, Any] | None,
    *,
    session_id: str | None = None,
    mode: str = "editor",
    _contracts: Mapping[str, Mapping[str, Any]] | None = None,
    _denied: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate and map one canonical request to its package-owned wire route."""

    if not isinstance(command_id, str) or not command_id:
        raise CommandPreflightError("command id must be a non-empty string")
    denied = (
        _load_deny_policy()
        if _denied is None
        else _denied
    ).get(command_id)
    if denied is not None:
        raise CommandPreflightError(
            f"command {command_id} is denied: {denied['reason']} "
            "Automatic fallback through snippets or raw exec is prohibited"
        )
    contract = (
        _contracts_by_id(snapshot)
        if _contracts is None
        else _contracts
    ).get(command_id)
    if contract is None:
        raise CommandPreflightError(f"unknown command id: {command_id}")
    requirements = contract["requirements"]
    if requirements.get("editor") and mode != "editor":
        raise CommandPreflightError(
            f"{command_id} requires editor mode"
        )
    if requirements.get("sessionId") and (
        not isinstance(session_id, str) or not session_id.strip()
    ):
        raise CommandPreflightError(
            f"{command_id} requires an explicit --session id"
        )
    if isinstance(args, Mapping):
        raw_args = args
    else:
        raise CommandPreflightError(f"{command_id} args must be an object")

    argument_list = contract["arguments"]
    canonical_names = [
        argument["name"]
        for argument in argument_list
    ]
    normalized: dict[str, Any] = {}
    for supplied_name, value in raw_args.items():
        if not isinstance(supplied_name, str):
            raise CommandPreflightError(
                f"{command_id} argument names must be strings"
            )
        canonical_name = next(
            (
                candidate
                for candidate in canonical_names
                if _ordinal_ignore_case_equal(supplied_name, candidate)
            ),
            None,
        )
        if canonical_name is None:
            raise CommandPreflightError(
                f"{command_id} has unknown argument: {supplied_name}"
            )
        if canonical_name in normalized:
            raise CommandPreflightError(
                f"{command_id} repeats argument: {canonical_name}"
            )
        normalized[canonical_name] = value

    arguments = {
        argument["name"]: argument
        for argument in argument_list
    }
    bound_values: dict[str, Any] = {}
    for name, argument in arguments.items():
        if name not in normalized:
            if argument.get("required"):
                raise CommandPreflightError(
                    f"{command_id} requires argument {name}"
                )
            if argument.get("hasDefault"):
                bound_values[name] = json.loads(argument["defaultJson"])
            continue
        value = normalized[name]
        _validate_schema(
            value,
            argument["schema"],
            f"{command_id}.{name}",
        )
        bound_value = _normalize_contract_value(
            value,
            argument["schema"],
        )
        _validate_constraints(
            bound_value,
            {
                "schema": argument["schema"],
                "nonEmpty": argument.get("nonEmpty", False),
                **(
                    {"minimum": argument["minimum"]}
                    if argument.get("hasMinimum")
                    else {}
                ),
                **(
                    {"maximum": argument["maximum"]}
                    if argument.get("hasMaximum")
                    else {}
                ),
                "allowedValues": argument.get("allowedValues", []),
                "allowedValuesIgnoreCase": argument.get(
                    "allowedValuesIgnoreCase",
                    False,
                ),
            },
            f"{command_id}.{name}",
        )
        bound_values[name] = bound_value

    supplied = set(normalized)
    _validate_rules(
        command_id,
        contract.get("rules", []),
        supplied,
        bound_values,
        arguments,
    )
    return {
        "id": command_id,
        "partition": contract["partition"],
        "wire": {
            "commandNamespace": contract["wire"]["commandNamespace"],
            "action": contract["wire"]["action"],
        },
        "args": {
            name: _normalize_wire_value(
                value,
                arguments[name]["schema"],
            )
            for name, value in normalized.items()
        },
    }


def prepare_batch(
    snapshot: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    *,
    session_id: str | None = None,
    mode: str = "editor",
) -> list[dict[str, Any]]:
    """Validate every canonical batch item before any command is dispatched."""

    if not isinstance(items, list) or not items:
        raise CommandPreflightError("batch commands must be a non-empty array")
    contracts = _contracts_by_id(snapshot)
    denied = _load_deny_policy()
    prepared = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise CommandPreflightError(
                f"batch command {index} must be an object"
            )
        fields = set(item)
        expected_fields = {"id", "args"}
        if fields != expected_fields:
            unknown_fields = sorted(fields - expected_fields)
            missing_fields = sorted(expected_fields - fields)
            details = []
            if unknown_fields:
                details.append("unknown field(s): " + ", ".join(unknown_fields))
            if missing_fields:
                details.append("missing field(s): " + ", ".join(missing_fields))
            raise CommandPreflightError(
                f"batch command {index} needs exactly id and args; "
                + "; ".join(details)
            )
        try:
            prepared.append(
                prepare_command(
                    snapshot,
                    item.get("id"),
                    item.get("args"),
                    session_id=session_id,
                    mode=mode,
                    _contracts=contracts,
                    _denied=denied,
                )
            )
        except CommandPreflightError as exc:
            raise CommandPreflightError(
                f"batch command {index}: {exc}"
            ) from exc
    return prepared
