"""Validation and canonical hashing for command registry schema v1.

Snapshot validation is structural: the CLI treats ``registryGeneration`` as an
opaque token and never recomputes fingerprints while resolving a registry.
The canonical hashing mirror of the package's binary protocol is kept for the
shared custom-command catalog, offline tooling, and the cross-implementation
serializer tests.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct


__all__ = (
    "RegistryProtocolError",
    "SCHEMA_VERSION",
    "compute_partition_fingerprint",
    "compute_registry_generation",
    "validate_fingerprint",
    "validate_snapshot",
    "validate_unchanged_response",
)

SCHEMA_VERSION = 1

_PARTITIONS = ("builtin", "custom")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_INT32_MAX = (1 << 31) - 1
_SCHEMA_KINDS = {
    "array",
    "boolean",
    "empty",
    "enum",
    "integer",
    "map",
    "number",
    "object",
    "reference",
    "string",
}
_RULE_KINDS = {
    "atLeastOneMutation",
    "atLeastOneOf",
    "atMostOneOf",
    "exactlyOneOf",
    "requiresWhen",
}


class RegistryProtocolError(ValueError):
    """Raised when registry data does not conform to schema v1."""


class _CanonicalWriter:
    def __init__(self):
        self._buffer = bytearray()

    def write_bool(self, value):
        self._buffer.extend(b"\x01" if value else b"\x00")

    def write_double(self, value):
        number = _finite_number(value, "canonical number")
        if number == 0.0:
            number = 0.0
        self._buffer.extend(struct.pack("<d", number))

    def write_int32(self, value):
        _nonnegative_int32(value, "canonical count")
        self._buffer.extend(struct.pack("<i", value))

    def write_string(self, value):
        if not isinstance(value, str):
            raise RegistryProtocolError("canonical string must be a string")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise RegistryProtocolError(
                "canonical string must contain valid Unicode"
            ) from error
        if len(encoded) > _INT32_MAX:
            raise RegistryProtocolError("canonical string is too large")
        self.write_int32(len(encoded))
        self._buffer.extend(encoded)

    def digest(self):
        return hashlib.sha256(self._buffer).hexdigest()


def validate_fingerprint(value):
    """Validate a bare SHA-256 partition fingerprint digest and return it."""

    return _validate_digest(value)


def _validate_digest(value):
    if not isinstance(value, str) or _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise RegistryProtocolError(
            "fingerprint must be a lowercase 64-character SHA-256 hex digest"
        )
    return value


def compute_partition_fingerprint(partition, commands):
    """Compute the package-compatible fingerprint for one registry partition."""

    if partition not in _PARTITIONS:
        raise RegistryProtocolError("partition must be 'builtin' or 'custom'")
    _validate_commands(commands, partition, "commands")

    writer = _CanonicalWriter()
    writer.write_int32(SCHEMA_VERSION)
    writer.write_string(partition)
    writer.write_int32(len(commands))
    for command in commands:
        _write_command(writer, command)
    return writer.digest()


def compute_registry_generation(
    builtin_count,
    builtin_fp,
    custom_count,
    custom_fp,
):
    """Compute the package-compatible generation for two partition identities."""

    _nonnegative_int32(builtin_count, "builtin count")
    _nonnegative_int32(custom_count, "custom count")
    _validate_digest(builtin_fp)
    _validate_digest(custom_fp)

    writer = _CanonicalWriter()
    writer.write_int32(SCHEMA_VERSION)
    writer.write_int32(builtin_count)
    writer.write_string(builtin_fp)
    writer.write_int32(custom_count)
    writer.write_string(custom_fp)
    return writer.digest()


def validate_snapshot(
    value,
    required_included=None,
):
    """Structurally validate a schema-v1 snapshot and return it unchanged.

    ``required_included`` may be one partition name or an iterable of names.
    ``registryGeneration`` and the partition fingerprints are checked for wire
    shape only; the CLI treats the generation as an opaque token and does not
    recompute canonical hashes during resolution.
    """

    snapshot = _exact_dict(
        value,
        "snapshot",
        {"schemaVersion", "registryGeneration", "builtin", "custom"},
    )
    _schema_version(snapshot["schemaVersion"], "snapshot.schemaVersion")
    _nonempty_string(snapshot["registryGeneration"], "snapshot.registryGeneration")

    required = _required_partitions(required_included)
    all_ids = set()
    for partition in _PARTITIONS:
        path = "snapshot." + partition
        item = _validate_partition(snapshot[partition], partition, path)
        if partition in required and not item["included"]:
            raise RegistryProtocolError(
                "{} must include its commands".format(path)
            )
        for command in item["commands"]:
            command_id = command["id"]
            if command_id in all_ids:
                raise RegistryProtocolError(
                    "snapshot contains duplicate command id {!r}".format(command_id)
                )
            all_ids.add(command_id)

    return value


def validate_unchanged_response(value):
    """Validate the conditional snapshot operation's unchanged answer."""

    payload = _exact_dict(
        value,
        "unchanged registry answer",
        {"schemaVersion", "registryGeneration", "unchanged"},
    )
    _schema_version(payload["schemaVersion"], "unchanged answer.schemaVersion")
    _nonempty_string(
        payload["registryGeneration"],
        "unchanged answer.registryGeneration",
    )
    if payload["unchanged"] is not True:
        raise RegistryProtocolError(
            "unchanged answer must set unchanged to true"
        )
    return payload


def _validate_partition(value, partition, path):
    item = _exact_dict(
        value,
        path,
        {"included", "count", "fingerprint", "commands"},
    )
    _boolean(item["included"], path + ".included")
    _nonnegative_int32(item["count"], path + ".count")
    _validate_digest(item["fingerprint"])

    commands = item["commands"]
    _validate_commands(commands, partition, path + ".commands")
    if item["included"]:
        if item["count"] != len(commands):
            raise RegistryProtocolError(
                "{}.count must equal the number of included commands".format(path)
            )
    elif commands:
        raise RegistryProtocolError(
            "{}.commands must be empty when the partition is not included".format(path)
        )
    return item


def _required_partitions(value):
    if value is None:
        return set()
    if isinstance(value, str):
        values = (value,)
    else:
        try:
            values = tuple(value)
        except TypeError as error:
            raise RegistryProtocolError(
                "required_included must contain registry partition names"
            ) from error
    required = set()
    for partition in values:
        if partition not in _PARTITIONS:
            raise RegistryProtocolError(
                "required_included may contain only 'builtin' and 'custom'"
            )
        required.add(partition)
    return required


def _validate_commands(commands, partition, path):
    _list(commands, path)
    _nonnegative_int32(len(commands), path + " length")
    previous_key = None
    ids = set()
    for index, command in enumerate(commands):
        command_path = "{}[{}]".format(path, index)
        _validate_command(command, partition, command_path)
        command_id = command["id"]
        if command_id in ids:
            raise RegistryProtocolError(
                "{} contains duplicate command id {!r}".format(path, command_id)
            )
        ids.add(command_id)
        key = _ordinal_key(command_id, command_path + ".id")
        if previous_key is not None and key <= previous_key:
            raise RegistryProtocolError(
                "{} must be sorted by command id using ordinal order".format(path)
            )
        previous_key = key
    return commands


def _validate_command(command, partition, path):
    command = _exact_dict(
        command,
        path,
        {
            "id",
            "wire",
            "summary",
            "partition",
            "requirements",
            "arguments",
            "result",
            "rules",
        },
    )
    _nonempty_string(command["id"], path + ".id")
    _string(command["summary"], path + ".summary")
    _string(command["partition"], path + ".partition")
    if command["partition"] != partition:
        raise RegistryProtocolError(
            "{}.partition must be {!r}".format(path, partition)
        )

    wire = _exact_dict(
        command["wire"],
        path + ".wire",
        {"commandNamespace", "action"},
    )
    _string(wire["commandNamespace"], path + ".wire.commandNamespace")
    _string(wire["action"], path + ".wire.action")

    requirements = _exact_dict(
        command["requirements"],
        path + ".requirements",
        {"editor", "mainThread", "sessionId"},
    )
    for name in ("editor", "mainThread", "sessionId"):
        _boolean(requirements[name], path + ".requirements." + name)

    arguments = command["arguments"]
    _list(arguments, path + ".arguments")
    _nonnegative_int32(len(arguments), path + ".arguments length")
    argument_names = set()
    for index, argument in enumerate(arguments):
        argument_path = "{}.arguments[{}]".format(path, index)
        _validate_argument(argument, argument_path)
        name = argument["name"]
        if name in argument_names:
            raise RegistryProtocolError(
                "{} contains duplicate argument name {!r}".format(path, name)
            )
        argument_names.add(name)

    _validate_schema(command["result"], path + ".result")

    rules = command["rules"]
    _list(rules, path + ".rules")
    _nonnegative_int32(len(rules), path + ".rules length")
    previous_rule_key = None
    for index, rule in enumerate(rules):
        rule_path = "{}.rules[{}]".format(path, index)
        _validate_rule(rule, rule_path)
        rule_key = _rule_order_key(rule, rule_path)
        if previous_rule_key is not None and rule_key < previous_rule_key:
            raise RegistryProtocolError(
                "{}.rules must use canonical ordinal order".format(path)
            )
        previous_rule_key = rule_key


def _validate_argument(argument, path):
    argument = _dict_with_conditional_numbers(
        argument,
        path,
        {
            "name",
            "schema",
            "required",
            "hasDefault",
            "defaultJson",
            "nonEmpty",
            "hasMinimum",
            "hasMaximum",
            "allowedValues",
            "allowedValuesIgnoreCase",
        },
        "hasMinimum",
        "minimum",
        "hasMaximum",
        "maximum",
    )
    _nonempty_string(argument["name"], path + ".name")
    _validate_schema(argument["schema"], path + ".schema")
    for name in (
        "required",
        "hasDefault",
        "nonEmpty",
        "hasMinimum",
        "hasMaximum",
        "allowedValuesIgnoreCase",
    ):
        _boolean(argument[name], path + "." + name)
    _string(argument["defaultJson"], path + ".defaultJson")
    if argument["hasDefault"]:
        _validate_default_json(argument["defaultJson"], path + ".defaultJson")
    elif argument["defaultJson"] != "":
        raise RegistryProtocolError(
            "{}.defaultJson must be empty when hasDefault is false".format(path)
        )
    if argument["hasMinimum"]:
        _finite_number(argument["minimum"], path + ".minimum")
    if argument["hasMaximum"]:
        _finite_number(argument["maximum"], path + ".maximum")
    _string_list(
        argument["allowedValues"],
        path + ".allowedValues",
        sorted_ordinal=True,
    )


def _validate_rule(rule, path):
    rule = _exact_dict(
        rule,
        path,
        {"kind", "arguments", "whenArgument", "whenEqualsJson", "requires"},
    )
    _string(rule["kind"], path + ".kind")
    if rule["kind"] not in _RULE_KINDS:
        raise RegistryProtocolError(
            "{}.kind is not a schema-v1 command rule".format(path)
        )
    _string_list(rule["arguments"], path + ".arguments")
    _string(rule["whenArgument"], path + ".whenArgument")
    _string(rule["whenEqualsJson"], path + ".whenEqualsJson")
    _string_list(rule["requires"], path + ".requires")


def _validate_schema(schema, path):
    _validate_schema_node(schema, path, is_root=True)
    definitions = schema.get("$defs", {})
    references = set()
    _collect_references(schema, references, traverse_definitions=False)
    pending = list(references)
    visited = set()
    while pending:
        reference = pending.pop(0)
        if reference not in definitions:
            raise RegistryProtocolError(
                "{} contains dangling schema reference {!r}".format(path, reference)
            )
        if reference in visited:
            continue
        visited.add(reference)
        before = set(references)
        _collect_references(
            definitions[reference],
            references,
            traverse_definitions=False,
        )
        pending.extend(
            item
            for item in references - before
            if item not in visited and item not in pending
        )
    unused = set(definitions) - references
    if unused:
        first = min(unused, key=lambda item: _ordinal_key(item, path))
        raise RegistryProtocolError(
            "{} contains unused schema definition {!r}".format(path, first)
        )


def _validate_schema_node(schema, path, is_root):
    schema = _dict_with_optional(
        schema,
        path,
        {"kind", "format", "nullable", "enumValues", "fields"},
        {"$ref", "items", "$defs"} if is_root else {"$ref", "items"},
    )
    _string(schema["kind"], path + ".kind")
    if schema["kind"] not in _SCHEMA_KINDS:
        raise RegistryProtocolError(
            "{}.kind is not a schema-v1 value kind".format(path)
        )
    _string(schema["format"], path + ".format")
    _boolean(schema["nullable"], path + ".nullable")

    if schema["kind"] == "reference":
        if "$ref" not in schema:
            raise RegistryProtocolError(
                "{} requires $ref for reference schemas".format(path)
            )
        _nonempty_string(schema["$ref"], path + ".$ref")
    elif "$ref" in schema:
        raise RegistryProtocolError(
            "{} may declare $ref only for a reference schema".format(path)
        )

    needs_items = schema["kind"] in {"array", "map"}
    if needs_items and "items" not in schema:
        raise RegistryProtocolError(
            "{} requires items for {} schemas".format(path, schema["kind"])
        )
    if not needs_items and "items" in schema:
        raise RegistryProtocolError(
            "{} may declare items only for array or map schemas".format(path)
        )
    if "items" in schema:
        _validate_schema_node(schema["items"], path + ".items", is_root=False)

    _string_list(
        schema["enumValues"],
        path + ".enumValues",
        sorted_ordinal=True,
    )
    fields = schema["fields"]
    _list(fields, path + ".fields")
    _nonnegative_int32(len(fields), path + ".fields length")
    if schema["kind"] != "object" and fields:
        raise RegistryProtocolError(
            "{}.fields must be empty for non-object schemas".format(path)
        )
    previous_key = None
    field_names = set()
    for index, field in enumerate(fields):
        field_path = "{}.fields[{}]".format(path, index)
        _validate_schema_field(field, field_path)
        name = field["name"]
        if name in field_names:
            raise RegistryProtocolError(
                "{} contains duplicate schema field {!r}".format(path, name)
            )
        field_names.add(name)
        key = _ordinal_key(name, field_path + ".name")
        if previous_key is not None and key <= previous_key:
            raise RegistryProtocolError(
                "{}.fields must use canonical ordinal order".format(path)
            )
        previous_key = key

    if "$defs" in schema:
        definitions = schema["$defs"]
        if not isinstance(definitions, dict):
            raise RegistryProtocolError("{}.$defs must be an object".format(path))
        if not definitions:
            raise RegistryProtocolError("{}.$defs must not be empty".format(path))
        previous_key = None
        for definition_id, definition_schema in definitions.items():
            _nonempty_string(definition_id, path + ".$defs key")
            key = _ordinal_key(definition_id, path + ".$defs key")
            if previous_key is not None and key <= previous_key:
                raise RegistryProtocolError(
                    "{}.$defs must use canonical ordinal order".format(path)
                )
            previous_key = key
            _validate_schema_node(
                definition_schema,
                "{}.$defs[{!r}]".format(path, definition_id),
                is_root=False,
            )


def _validate_schema_field(field, path):
    field = _dict_with_optional(
        field,
        path,
        {"name", "schema"},
        {
            "required",
            "nonEmpty",
            "minimum",
            "maximum",
            "allowedValues",
            "allowedValuesIgnoreCase",
        },
    )
    _nonempty_string(field["name"], path + ".name")
    _validate_schema_node(field["schema"], path + ".schema", is_root=False)
    for flag in ("required", "nonEmpty", "allowedValuesIgnoreCase"):
        if flag in field:
            _boolean(field[flag], path + "." + flag)
            if not field[flag]:
                raise RegistryProtocolError(
                    "{}.{} is emitted only when true".format(path, flag)
                )
    for bound in ("minimum", "maximum"):
        if bound in field:
            _finite_number(field[bound], path + "." + bound)
    if "allowedValues" in field:
        _string_list(
            field["allowedValues"],
            path + ".allowedValues",
            sorted_ordinal=True,
        )
        if not field["allowedValues"]:
            raise RegistryProtocolError(
                "{}.allowedValues is emitted only when non-empty".format(path)
            )


def _collect_references(schema, references, traverse_definitions):
    if schema.get("kind") == "reference":
        references.add(schema["$ref"])
    if "items" in schema:
        _collect_references(
            schema["items"],
            references,
            traverse_definitions=False,
        )
    for field in schema["fields"]:
        _collect_references(
            field["schema"],
            references,
            traverse_definitions=False,
        )
    if traverse_definitions:
        for definition in schema.get("$defs", {}).values():
            _collect_references(
                definition,
                references,
                traverse_definitions=False,
            )


def _write_command(writer, command):
    writer.write_string(command["id"])
    writer.write_string(command["wire"]["commandNamespace"])
    writer.write_string(command["wire"]["action"])
    writer.write_string(command["summary"])
    writer.write_string(command["partition"])
    writer.write_bool(command["requirements"]["editor"])
    writer.write_bool(command["requirements"]["mainThread"])
    writer.write_bool(command["requirements"]["sessionId"])

    writer.write_int32(len(command["arguments"]))
    for argument in command["arguments"]:
        writer.write_string(argument["name"])
        _write_schema(writer, argument["schema"])
        writer.write_bool(argument["required"])
        writer.write_bool(argument["hasDefault"])
        writer.write_string(argument["defaultJson"])
        writer.write_bool(argument["nonEmpty"])
        writer.write_bool(argument["hasMinimum"])
        if argument["hasMinimum"]:
            writer.write_double(argument["minimum"])
        writer.write_bool(argument["hasMaximum"])
        if argument["hasMaximum"]:
            writer.write_double(argument["maximum"])
        _write_strings(writer, argument["allowedValues"])
        writer.write_bool(argument["allowedValuesIgnoreCase"])

    _write_schema(writer, command["result"])
    writer.write_int32(len(command["rules"]))
    for rule in command["rules"]:
        writer.write_string(rule["kind"])
        _write_strings(writer, rule["arguments"])
        writer.write_string(rule["whenArgument"])
        writer.write_string(rule["whenEqualsJson"])
        _write_strings(writer, rule["requires"])


def _write_schema(writer, schema):
    writer.write_string(schema["kind"])
    writer.write_string(schema["format"])
    writer.write_bool(schema["nullable"])
    writer.write_string(schema.get("$ref", ""))
    writer.write_bool("items" in schema)
    if "items" in schema:
        _write_schema(writer, schema["items"])

    fields = schema["fields"]
    writer.write_int32(len(fields))
    for field in fields:
        writer.write_string(field["name"])
        _write_schema(writer, field["schema"])
        writer.write_bool(field.get("required", False))
        writer.write_bool(field.get("nonEmpty", False))
        writer.write_bool("minimum" in field)
        if "minimum" in field:
            writer.write_double(field["minimum"])
        writer.write_bool("maximum" in field)
        if "maximum" in field:
            writer.write_double(field["maximum"])
        _write_strings(writer, field.get("allowedValues", []))
        writer.write_bool(field.get("allowedValuesIgnoreCase", False))

    _write_strings(writer, schema["enumValues"])
    definitions = schema.get("$defs", {})
    writer.write_int32(len(definitions))
    for definition_id, definition_schema in definitions.items():
        writer.write_string(definition_id)
        _write_schema(writer, definition_schema)


def _write_strings(writer, values):
    writer.write_int32(len(values))
    for value in values:
        writer.write_string(value)


def _schema_version(value, path):
    if type(value) is not int or value != SCHEMA_VERSION:
        raise RegistryProtocolError(
            "{} must equal schema version {}".format(path, SCHEMA_VERSION)
        )
    return value


def _boolean(value, path):
    if type(value) is not bool:
        raise RegistryProtocolError("{} must be a boolean".format(path))
    return value


def _nonnegative_int32(value, path):
    if type(value) is not int or value < 0 or value > _INT32_MAX:
        raise RegistryProtocolError(
            "{} must be a non-negative int32".format(path)
        )
    return value


def _finite_number(value, path):
    if type(value) not in (int, float):
        raise RegistryProtocolError("{} must be a number".format(path))
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise RegistryProtocolError("{} must be finite".format(path)) from error
    if not math.isfinite(number):
        raise RegistryProtocolError("{} must be finite".format(path))
    return number


def _string(value, path):
    if not isinstance(value, str):
        raise RegistryProtocolError("{} must be a string".format(path))
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise RegistryProtocolError(
            "{} must contain valid Unicode".format(path)
        ) from error
    return value


def _nonempty_string(value, path):
    _string(value, path)
    if value == "":
        raise RegistryProtocolError("{} must not be empty".format(path))
    return value


def _list(value, path):
    if not isinstance(value, list):
        raise RegistryProtocolError("{} must be an array".format(path))
    return value


def _string_list(value, path, sorted_ordinal=False):
    _list(value, path)
    _nonnegative_int32(len(value), path + " length")
    previous = None
    for index, item in enumerate(value):
        item_path = "{}[{}]".format(path, index)
        _string(item, item_path)
        if sorted_ordinal:
            key = _ordinal_key(item, item_path)
            if previous is not None and key < previous:
                raise RegistryProtocolError(
                    "{} must use canonical ordinal order".format(path)
                )
            previous = key
    return value


def _ordinal_key(value, path):
    _string(value, path)
    encoded = value.encode("utf-16-le")
    if not encoded:
        return ()
    return struct.unpack("<{}H".format(len(encoded) // 2), encoded)


def _rule_order_key(rule, path):
    return (
        _ordinal_key(rule["kind"], path + ".kind"),
        tuple(_ordinal_key(item, path + ".arguments") for item in rule["arguments"]),
        _ordinal_key(rule["whenArgument"], path + ".whenArgument"),
        _ordinal_key(rule["whenEqualsJson"], path + ".whenEqualsJson"),
        tuple(_ordinal_key(item, path + ".requires") for item in rule["requires"]),
    )


def _validate_default_json(value, path):
    if value == "":
        raise RegistryProtocolError("{} must contain encoded JSON".format(path))

    def reject_constant(constant):
        raise ValueError("non-finite constant {}".format(constant))

    try:
        parsed = json.loads(value, parse_constant=reject_constant)
        pending = [parsed]
        while pending:
            item = pending.pop()
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("non-finite number")
            if isinstance(item, list):
                pending.extend(item)
            elif isinstance(item, dict):
                pending.extend(item.values())
    except (RecursionError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RegistryProtocolError(
            "{} must contain finite valid JSON".format(path)
        ) from error


def _exact_dict(value, path, keys):
    return _dict_with_optional(value, path, keys, set())


def _dict_with_optional(value, path, required, optional):
    if not isinstance(value, dict):
        raise RegistryProtocolError("{} must be an object".format(path))
    actual = set(value)
    missing = required - actual
    unexpected = actual - required - optional
    if missing:
        raise RegistryProtocolError(
            "{} is missing field(s): {}".format(path, ", ".join(sorted(missing)))
        )
    if unexpected:
        raise RegistryProtocolError(
            "{} contains unexpected field(s): {}".format(
                path,
                ", ".join(sorted(str(item) for item in unexpected)),
            )
        )
    return value


def _dict_with_conditional_numbers(
    value,
    path,
    required,
    first_flag,
    first_number,
    second_flag,
    second_number,
):
    value = _dict_with_optional(
        value,
        path,
        required,
        {first_number, second_number},
    )
    for flag, number in (
        (first_flag, first_number),
        (second_flag, second_number),
    ):
        _boolean(value[flag], path + "." + flag)
        if value[flag] != (number in value):
            raise RegistryProtocolError(
                "{}.{} presence must match {}".format(path, number, flag)
            )
    return value
