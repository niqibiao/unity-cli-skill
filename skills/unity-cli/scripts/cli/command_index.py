"""Static routing contracts for the built-in Unity command protocol.

The Unity service remains the execution authority.  This module supplies the
agent-facing metadata that reflection cannot express (visibility, intent
boundaries, required arguments, and verification) and rejects requests that
the service would otherwise accept as silent no-ops.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


MANIFEST_PATH = Path(__file__).with_name("command_manifest.json")
TIERS = ("core", "advanced", "control-plane")
DOMAINS = (
    "editor",
    "scene",
    "objects",
    "assets",
    "prefabs",
    "capture",
    "tests",
    "control",
)


class CommandContractError(ValueError):
    """A built-in command request violates the committed routing contract."""


def load_manifest(path=None):
    """Load the committed built-in command manifest."""
    manifest_path = Path(path) if path else MANIFEST_PATH
    try:
        data = json.loads(manifest_path.read_text("utf-8"))
    except OSError as exc:
        raise CommandContractError(f"cannot read command manifest: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CommandContractError(f"invalid command manifest JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("commands"), list):
        raise CommandContractError("command manifest needs a commands array")
    return data


def command_contracts(path=None):
    """Return built-in contracts keyed by canonical ``namespace/action`` id."""
    contracts = {}
    for contract in load_manifest(path)["commands"]:
        command_id = contract.get("id")
        if not isinstance(command_id, str) or not command_id:
            raise CommandContractError("every command manifest entry needs a non-empty id")
        if command_id in contracts:
            raise CommandContractError(f"duplicate command contract: {command_id}")
        contracts[command_id] = contract
    return contracts


def live_command_id(command):
    """Return the canonical id for a live list-commands descriptor."""
    namespace = command.get("commandNamespace") or command.get("namespace")
    action = command.get("action")
    if isinstance(namespace, str) and isinstance(action, str):
        return f"{namespace}/{action}"
    live_id = command.get("id")
    if isinstance(live_id, str):
        return live_id.replace(".", "/", 1) if "/" not in live_id else live_id
    return ""


def annotate_live_command(command, contracts=None):
    """Add routing metadata to a live descriptor without replacing live schema."""
    if contracts is None:
        contracts = command_contracts()
    annotated = dict(command)
    command_id = live_command_id(command)
    if command_id:
        annotated["canonicalId"] = command_id
    contract = contracts.get(command_id)
    if contract:
        annotated["domain"] = contract["domain"]
        annotated["tier"] = contract["tier"]
        annotated["availability"] = contract["availability"]
    elif command.get("commandType") == "custom":
        annotated["domain"] = "custom"
        annotated["tier"] = "advanced"
        annotated["availability"] = "supported"
    return annotated


def contract_descriptors(contracts=None):
    """Return manifest entries in the same broad shape as live descriptors."""
    if contracts is None:
        contracts = command_contracts()
    descriptors = []
    for command_id, contract in contracts.items():
        descriptor = dict(contract)
        descriptor["canonicalId"] = command_id
        descriptor["commandNamespace"] = contract["ns"]
        descriptor["commandType"] = "builtin"
        descriptor["arguments"] = contract.get("args", [])
        descriptors.append(descriptor)
    return descriptors


def filter_live_commands(
    commands,
    *,
    type_filter="all",
    domain=None,
    tier=None,
    command_id=None,
    include_blocked=False,
    contracts=None,
):
    """Annotate and filter live command descriptors.

    Unknown built-ins are retained when no domain/tier filter is requested so a
    newer package remains discoverable.  They are never mistaken for a
    classified core command.
    """
    if contracts is None:
        contracts = command_contracts()
    filtered = []
    for raw in commands:
        item = annotate_live_command(raw, contracts)
        command_type = item.get("commandType", "builtin")
        if type_filter != "all" and command_type != type_filter:
            continue
        if not include_blocked and item.get("availability") == "blocked":
            continue
        if domain is not None and item.get("domain") != domain:
            continue
        if tier is not None and item.get("tier") != tier:
            continue
        if command_id is not None and item.get("canonicalId") != command_id:
            continue
        filtered.append(item)
    return filtered


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool))


def _utf16_code_units(value):
    """Return the length used by C# ``string.Length``."""
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _present(args, name, arg_specs):
    """Whether an argument carries a meaningful, non-sentinel value."""
    if name not in args:
        return False
    value = args[name]
    if value is None or value == "":
        return False
    spec = arg_specs.get(name, {})
    if "sentinel" in spec and value == spec["sentinel"]:
        return False
    if "default" in spec and value == spec["default"]:
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    return True


def _validate_vector3(name, value):
    if not isinstance(value, dict):
        raise CommandContractError(f"{name} must be a Vector3 object")
    expected = {"x", "y", "z"}
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown or missing:
        details = []
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"unknown {', '.join(sorted(unknown))}")
        raise CommandContractError(f"{name} must contain exactly x/y/z ({'; '.join(details)})")
    if not all(_is_number(value[axis]) for axis in expected):
        raise CommandContractError(f"{name}.x/y/z must be numbers")


def _validate_field_pairs(name, value):
    if not isinstance(value, list) or not value:
        raise CommandContractError(f"{name} must be a non-empty FieldPair array")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise CommandContractError(f"{name}[{index}] must be an object")
        unknown = set(item) - {"name", "value"}
        if unknown:
            raise CommandContractError(
                f"{name}[{index}] has unknown field(s): {', '.join(sorted(unknown))}"
            )
        if not isinstance(item.get("name"), str) or not item["name"]:
            raise CommandContractError(f"{name}[{index}].name must be a non-empty string")
        if not isinstance(item.get("value"), str):
            raise CommandContractError(
                f"{name}[{index}].value must be a string; encode Vector/Color values as JSON text"
            )


def _validate_type(spec, value):
    name = spec["name"]
    type_name = spec["type"]
    if type_name == "string":
        valid = isinstance(value, str)
    elif type_name == "int":
        valid = _is_int(value)
    elif type_name == "bool":
        valid = isinstance(value, bool)
    elif type_name == "string[]":
        valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
    elif type_name == "int[]":
        valid = isinstance(value, list) and all(_is_int(item) for item in value)
    elif type_name == "Vector3":
        _validate_vector3(name, value)
        valid = True
    elif type_name == "FieldPair[]":
        _validate_field_pairs(name, value)
        valid = True
    else:
        raise CommandContractError(f"manifest has unsupported type {type_name!r} for {name}")
    if not valid:
        raise CommandContractError(f"{name} must be {type_name}")

    if spec.get("nonEmpty"):
        if isinstance(value, str) and not value.strip():
            raise CommandContractError(f"{name} must not be empty")
        if isinstance(value, (list, dict)) and not value:
            raise CommandContractError(f"{name} must not be empty")
    if "enum" in spec:
        choices = spec["enum"]
        if spec.get("caseInsensitive") and isinstance(value, str):
            enum_match = any(
                isinstance(choice, str) and value.casefold() == choice.casefold()
                for choice in choices
            )
        else:
            enum_match = value in choices
        if not enum_match:
            rendered = ", ".join(repr(choice) for choice in choices)
            raise CommandContractError(f"{name} must be one of: {rendered}")
    if "pattern" in spec and isinstance(value, str):
        if re.fullmatch(spec["pattern"], value) is None:
            pattern_error = spec.get("patternError")
            if pattern_error:
                raise CommandContractError(f"{name} {pattern_error}")
            raise CommandContractError(f"{name} has an invalid format")
    measured = len(value) if isinstance(value, (str, list, dict)) else value
    if "min" in spec and measured < spec["min"]:
        unit = " items" if isinstance(value, list) else ""
        raise CommandContractError(f"{name} must have value/length >= {spec['min']}{unit}")
    if "max" in spec and measured > spec["max"]:
        unit = " items" if isinstance(value, list) else ""
        raise CommandContractError(f"{name} must have value/length <= {spec['max']}{unit}")
    if isinstance(value, list):
        if spec.get("itemNonEmpty"):
            for index, item in enumerate(value):
                if isinstance(item, str) and not item.strip():
                    raise CommandContractError(f"{name}[{index}] must not be empty")
        if "itemMax" in spec:
            item_max = spec["itemMax"]
            for index, item in enumerate(value):
                measured_item = (
                    _utf16_code_units(item)
                    if isinstance(item, str)
                    else len(item)
                    if isinstance(item, (list, dict))
                    else item
                )
                if measured_item > item_max:
                    raise CommandContractError(
                        f"{name}[{index}] must have value/length <= {item_max} "
                        "UTF-16 code units"
                    )


def _groups(value):
    if not value:
        return []
    if value and all(isinstance(item, str) for item in value):
        return [value]
    return value


def _validate_cross_field_rules(command_id, args, arg_specs, rules):
    for group in _groups(rules.get("exactlyOneOf")):
        present = [name for name in group if _present(args, name, arg_specs)]
        if len(present) != 1:
            raise CommandContractError(
                f"{command_id} needs exactly one of {', '.join(group)}"
            )

    for group in _groups(rules.get("atMostOneOf")):
        present = [name for name in group if _present(args, name, arg_specs)]
        if len(present) > 1:
            raise CommandContractError(
                f"{command_id} accepts at most one of {', '.join(group)}"
            )

    for group in _groups(rules.get("atLeastOne")):
        if not any(_present(args, name, arg_specs) for name in group):
            raise CommandContractError(
                f"{command_id} needs at least one of {', '.join(group)}"
            )

    mutations = rules.get("atLeastOneMutation", [])
    if mutations and not any(_present(args, name, arg_specs) for name in mutations):
        raise CommandContractError(
            f"{command_id} needs at least one mutation field: {', '.join(mutations)}"
        )

    conditional_rules = rules.get("requiresWhen", [])
    if isinstance(conditional_rules, dict):
        conditional_rules = [conditional_rules]
    for rule in conditional_rules:
        when = rule.get("when", rule)
        name = when.get("arg")
        if name in args and args[name] == when.get("equals"):
            missing = [
                required
                for required in rule.get("requires", [])
                if not _present(args, required, arg_specs)
            ]
            if missing:
                raise CommandContractError(
                    f"{command_id} requires {', '.join(missing)} when "
                    f"{name}={when.get('equals')!r}"
                )


def validate_command_request(namespace, action, args=None, *, session_id=None, contracts=None):
    """Validate one request; return normalized args.

    Custom commands are intentionally passed through because their schema is
    project-specific.  Built-ins fail closed before an HTTP request is made.
    """
    if contracts is None:
        contracts = command_contracts()
    command_id = f"{namespace}/{action}"
    contract = contracts.get(command_id)
    if contract is None:
        return args
    if contract.get("availability") == "blocked":
        raise CommandContractError(
            f"{command_id} is blocked: {contract.get('blockedReason', 'not operational')}"
        )
    specs = contract.get("args", [])
    virtual_specs = [spec for spec in specs if spec.get("type") == "request-session"]
    if (contract.get("requiresSession") or virtual_specs) and not session_id:
        raise CommandContractError(f"{command_id} requires an explicit --session id")

    if args is None:
        normalized = {}
    elif isinstance(args, str):
        try:
            normalized = json.loads(args)
        except json.JSONDecodeError as exc:
            raise CommandContractError(f"{command_id} args is invalid JSON: {exc}") from exc
    else:
        normalized = args
    if not isinstance(normalized, dict):
        raise CommandContractError(f"{command_id} args must be a JSON object")

    wire_specs = [spec for spec in specs if spec.get("type") != "request-session"]
    arg_specs = {spec["name"]: spec for spec in wire_specs}
    unknown = sorted(set(normalized) - set(arg_specs))
    if unknown:
        raise CommandContractError(
            f"{command_id} has unknown argument(s): {', '.join(unknown)}"
        )

    for spec in wire_specs:
        name = spec["name"]
        if spec.get("required") and not _present(normalized, name, arg_specs):
            raise CommandContractError(f"{command_id} requires non-empty {name}")
        if name in normalized:
            _validate_type(spec, normalized[name])

    _validate_cross_field_rules(
        command_id, normalized, arg_specs, contract.get("rules", {})
    )
    return normalized


def required_command_capabilities(namespace, action, *, contracts=None):
    """Return runtime capabilities required by one committed built-in contract."""
    if contracts is None:
        contracts = command_contracts()
    contract = contracts.get(f"{namespace}/{action}")
    if contract is None:
        return frozenset()
    required = contract.get("requiresCapabilities", [])
    if not isinstance(required, list) or any(
        not isinstance(item, str) or not item
        for item in required
    ):
        raise CommandContractError(
            f"{namespace}/{action} has invalid requiresCapabilities metadata"
        )
    return frozenset(required)


def command_allows_batch(namespace, action, *, contracts=None):
    """Whether one committed built-in contract may be sent through ``batch``."""
    if contracts is None:
        contracts = command_contracts()
    contract = contracts.get(f"{namespace}/{action}")
    if contract is None:
        return True
    allowed = contract.get("allowInBatch", True)
    if not isinstance(allowed, bool):
        raise CommandContractError(
            f"{namespace}/{action} has invalid allowInBatch metadata"
        )
    return allowed


def validate_batch_items(items, *, session_id=None, contracts=None):
    """Validate all recognized built-ins in a batch request."""
    if contracts is None:
        contracts = command_contracts()
    if not isinstance(items, list):
        raise CommandContractError("batch commands must be an array")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise CommandContractError(f"batch command {index} must be an object")
        namespace = item.get("ns", item.get("namespace"))
        action = item.get("action")
        if not isinstance(namespace, str) or not isinstance(action, str):
            raise CommandContractError(
                f"batch command {index} needs string ns/namespace and action"
            )
        if not command_allows_batch(
            namespace,
            action,
            contracts=contracts,
        ):
            raise CommandContractError(
                f"batch command {index}: {namespace}/{action} cannot run in batch; "
                "send it with cs command"
            )
        item_session_id = item.get("sessionId")
        if item_session_id is not None and (
            not isinstance(item_session_id, str) or not item_session_id.strip()
        ):
            raise CommandContractError(
                f"batch command {index} sessionId must be a non-empty string"
            )
        raw_args = item.get("args")
        raw_args_json = item.get("argsJson")
        if isinstance(raw_args, dict):
            args = raw_args
        else:
            args = raw_args_json or raw_args
            if args is None:
                args = {}
            elif not isinstance(args, str):
                selected_field = "argsJson" if raw_args_json else "args"
                expected_type = (
                    "a JSON string"
                    if selected_field == "argsJson"
                    else "a JSON object or JSON string"
                )
                raise CommandContractError(
                    f"batch command {index} {selected_field} must be {expected_type}"
                )
        try:
            validate_command_request(
                namespace,
                action,
                args,
                session_id=item_session_id or session_id,
                contracts=contracts,
            )
        except CommandContractError as exc:
            raise CommandContractError(f"batch command {index}: {exc}") from exc
    return items
