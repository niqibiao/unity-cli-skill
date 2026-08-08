"""Progressive, schema-owning command discovery projections.

The package registry snapshot is the only source of executable contracts.  This
module combines it with a small routing overlay and exposes three bounded views:

* no selector: a compact domain index;
* one or more domains: schema-free route cards;
* one or more canonical IDs: full contracts plus one direct relation layer.

``canonical-agent-v2`` preserves executable argument and rule semantics, while
projecting results to their top-level field inventory and omitting package/wire
diagnostics that an agent invoking canonical IDs does not use. ``package-v1``
returns the byte-shape supplied by the resolved snapshot.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_OVERLAY_PATH = Path(__file__).with_name("routing_overlay.json")
_EXECUTABLE_FIELDS = frozenset(
    {
        "arguments",
        "requirements",
        "result",
        "rules",
        "wire",
    }
)


class DiscoveryError(ValueError):
    """Raised when a discovery request or its source data is invalid."""

    def __init__(self, message: str, *, code: str = "invalid_discovery"):
        super().__init__(message)
        self.code = code


def _compact_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    result = {"kind": schema["kind"]}
    if schema.get("format"):
        result["format"] = schema["format"]
    if schema.get("nullable"):
        result["nullable"] = True
    if "$ref" in schema:
        result["$ref"] = schema["$ref"]
    if "items" in schema:
        result["items"] = _compact_schema(schema["items"])
    if schema.get("enumValues"):
        result["enumValues"] = copy.deepcopy(schema["enumValues"])
    if schema.get("fields"):
        result["fields"] = [
            _compact_field(field)
            for field in schema["fields"]
        ]
    if schema.get("$defs"):
        result["$defs"] = {
            name: _compact_schema(definition)
            for name, definition in schema["$defs"].items()
        }
    return result


def _compact_field(field: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "name": field["name"],
        "schema": _compact_schema(field["schema"]),
    }
    if field.get("required"):
        result["required"] = True
    if field.get("nonEmpty"):
        result["nonEmpty"] = True
    if "minimum" in field:
        result["minimum"] = field["minimum"]
    if "maximum" in field:
        result["maximum"] = field["maximum"]
    if field.get("allowedValues"):
        result["allowedValues"] = copy.deepcopy(field["allowedValues"])
    if field.get("allowedValuesIgnoreCase"):
        result["allowedValuesIgnoreCase"] = True
    return result


def _compact_argument(argument: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "name": argument["name"],
        "schema": _compact_schema(argument["schema"]),
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
        result["allowedValues"] = copy.deepcopy(argument["allowedValues"])
    if argument.get("allowedValuesIgnoreCase"):
        result["allowedValuesIgnoreCase"] = True
    return result


def _compact_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    result = {"kind": rule["kind"]}
    for field in ("arguments", "requires"):
        if rule.get(field):
            result[field] = list(rule[field])
    for field in ("whenArgument", "whenEqualsJson"):
        if rule.get(field):
            result[field] = rule[field]
    return result


def _compact_result_schema(
    schema: Mapping[str, Any],
    definitions: Mapping[str, Any] | None = None,
    resolving: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if definitions is None:
        definitions = schema.get("$defs", {})
    if schema["kind"] == "reference":
        reference = schema.get("$ref", "")
        target = definitions.get(reference)
        if not isinstance(target, Mapping):
            raise DiscoveryError(
                f"result schema contains unresolved reference {reference!r}"
            )
        if reference in resolving:
            result = {"kind": "reference", "opaque": True}
            if schema.get("nullable"):
                result["nullable"] = True
            return result
        result = _compact_result_schema(
            target,
            definitions,
            resolving | {reference},
        )
        if schema.get("nullable"):
            result["nullable"] = True
        else:
            result.pop("nullable", None)
        return result

    result = {"kind": schema["kind"]}
    if schema.get("format"):
        result["format"] = schema["format"]
    if schema.get("nullable"):
        result["nullable"] = True
    if "items" in schema:
        result["items"] = _compact_result_schema(
            schema["items"],
            definitions,
            resolving,
        )
    if schema.get("enumValues"):
        result["enumValues"] = copy.deepcopy(schema["enumValues"])
    if schema.get("fields"):
        result["fields"] = [
            field["name"]
            for field in schema["fields"]
        ]
    return result


def _project_contract(
    contract: Mapping[str, Any],
    detail: str,
) -> dict[str, Any]:
    if detail == "package":
        return copy.deepcopy(contract)
    return {
        "id": contract["id"],
        "requirements": {
            name: True
            for name, required in contract["requirements"].items()
            if required
        },
        "arguments": [
            _compact_argument(argument)
            for argument in contract["arguments"]
        ],
        "result": _compact_result_schema(contract["result"]),
        "rules": [
            _compact_rule(rule)
            for rule in contract["rules"]
        ],
    }


def _deduplicate(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _load_default_overlay() -> dict[str, Any]:
    try:
        return json.loads(_OVERLAY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoveryError(
            f"unable to load routing overlay {_OVERLAY_PATH}: {exc}"
        ) from exc


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DiscoveryError(f"{label} must be an object")
    return value


def _require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise DiscoveryError(f"{label} must be a list of non-empty strings")
    return list(value)


def _contracts_by_id(
    snapshot: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str]]:
    contracts: dict[str, Mapping[str, Any]] = {}
    partitions: dict[str, str] = {}
    for partition_name in ("builtin", "custom"):
        partition = _require_mapping(
            snapshot.get(partition_name, {}),
            f"snapshot.{partition_name}",
        )
        commands = partition.get("commands", [])
        if not isinstance(commands, list):
            raise DiscoveryError(
                f"snapshot.{partition_name}.commands must be a list"
            )
        for raw_contract in commands:
            contract = _require_mapping(
                raw_contract,
                f"snapshot.{partition_name}.commands entry",
            )
            command_id = contract.get("id")
            if not isinstance(command_id, str) or not command_id:
                raise DiscoveryError("registry contract is missing a canonical id")
            if command_id in contracts:
                raise DiscoveryError(
                    f"duplicate canonical command contract: {command_id}"
                )
            contracts[command_id] = contract
            partitions[command_id] = partition_name
    return contracts, partitions


def _validated_overlay(
    overlay: Mapping[str, Any],
    contracts: Mapping[str, Mapping[str, Any]],
) -> tuple[
    Mapping[str, Mapping[str, Any]],
    Mapping[str, Mapping[str, Any]],
    Mapping[str, int],
    Mapping[str, Mapping[str, Any]],
]:
    raw_domains = _require_mapping(overlay.get("domains"), "overlay.domains")
    raw_routes = _require_mapping(overlay.get("commands"), "overlay.commands")
    raw_counts = _require_mapping(
        overlay.get("expectedCounts"),
        "overlay.expectedCounts",
    )

    domains: dict[str, Mapping[str, Any]] = {}
    for domain_id, raw_domain in raw_domains.items():
        if not isinstance(domain_id, str) or not domain_id:
            raise DiscoveryError("overlay domain ids must be non-empty strings")
        domain = _require_mapping(
            raw_domain,
            f"overlay.domains.{domain_id}",
        )
        if not isinstance(domain.get("view"), str):
            raise DiscoveryError(f"domain {domain_id} is missing its view")
        if not isinstance(domain.get("summary"), str):
            raise DiscoveryError(f"domain {domain_id} is missing its summary")
        domains[domain_id] = domain

    routes: dict[str, Mapping[str, Any]] = {}
    for command_id, raw_route in raw_routes.items():
        if not isinstance(command_id, str) or not command_id:
            raise DiscoveryError("overlay command ids must be non-empty strings")
        if command_id not in contracts:
            raise DiscoveryError(
                f"routing overlay references unknown command: {command_id}",
                code="registry_incomplete",
            )
        route = _require_mapping(
            raw_route,
            f"overlay.commands.{command_id}",
        )
        duplicated = _EXECUTABLE_FIELDS.intersection(route)
        if duplicated:
            fields = ", ".join(sorted(duplicated))
            raise DiscoveryError(
                f"routing overlay duplicates executable fields for "
                f"{command_id}: {fields}"
            )
        view = route.get("view")
        if not isinstance(view, str) or not view:
            raise DiscoveryError(f"route {command_id} is missing its view")
        route_domains = _require_string_list(
            route.get("domains"),
            f"route {command_id}.domains",
        )
        for domain_id in route_domains:
            domain = domains.get(domain_id)
            if domain is None:
                raise DiscoveryError(
                    f"route {command_id} references unknown domain: {domain_id}"
                )
            if domain.get("view") != view:
                raise DiscoveryError(
                    f"route {command_id} and domain {domain_id} use "
                    f"different views"
                )
        if not isinstance(route.get("tier"), str) or not route.get("tier"):
            raise DiscoveryError(f"route {command_id} is missing its tier")
        if not isinstance(route.get("effect"), str) or not route.get("effect"):
            raise DiscoveryError(f"route {command_id} is missing its effect")
        if not isinstance(route.get("selectWhen"), str):
            raise DiscoveryError(f"route {command_id} is missing selectWhen")
        if not isinstance(route.get("avoidWhen"), str):
            raise DiscoveryError(f"route {command_id} is missing avoidWhen")
        for relation_name in ("prepareWith", "verifyWith", "limitations"):
            _require_string_list(
                route.get(relation_name),
                f"route {command_id}.{relation_name}",
            )
        routes[command_id] = route

    expected_counts: dict[str, int] = {}
    for view, count in raw_counts.items():
        if (
            not isinstance(view, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise DiscoveryError(
                "overlay expectedCounts must map view names to "
                "non-negative integers"
            )
        expected_counts[view] = count

    for command_id, route in routes.items():
        for relation_name in ("prepareWith", "verifyWith"):
            for related_id in route[relation_name]:
                if related_id not in routes:
                    raise DiscoveryError(
                        f"route {command_id}.{relation_name} references "
                        f"unknown command: {related_id}"
                    )

    raw_deny_policy = overlay.get("denyPolicy")
    if not isinstance(raw_deny_policy, list):
        raise DiscoveryError("overlay.denyPolicy must be a list")
    deny_policy: dict[str, Mapping[str, Any]] = {}
    for index, raw_policy in enumerate(raw_deny_policy):
        policy = _require_mapping(
            raw_policy,
            f"overlay.denyPolicy[{index}]",
        )
        denied_id = policy.get("id")
        if not isinstance(denied_id, str) or not denied_id:
            raise DiscoveryError(
                f"overlay.denyPolicy[{index}] is missing its command id"
            )
        if denied_id in routes:
            raise DiscoveryError(
                f"denied command also has a route: {denied_id}"
            )
        if denied_id in deny_policy:
            raise DiscoveryError(f"duplicate deny policy: {denied_id}")
        for field in ("intent", "reason"):
            if not isinstance(policy.get(field), str) or not policy[field]:
                raise DiscoveryError(
                    f"deny policy {denied_id} is missing {field}"
                )
        policy_domains = _require_string_list(
            policy.get("domains"),
            f"deny policy {denied_id}.domains",
        )
        if not policy_domains:
            raise DiscoveryError(
                f"deny policy {denied_id} needs at least one domain"
            )
        unknown_domains = [
            domain_id
            for domain_id in policy_domains
            if domain_id not in domains
        ]
        if unknown_domains:
            raise DiscoveryError(
                f"deny policy {denied_id} references unknown domains: "
                f"{', '.join(unknown_domains)}"
            )
        if policy.get("tier") not in ("core", "advanced"):
            raise DiscoveryError(
                f"deny policy {denied_id} needs a core or advanced tier"
            )
        if policy.get("fallbackPolicy") != "prohibited":
            raise DiscoveryError(
                f"deny policy {denied_id} must prohibit fallback"
            )
        deny_policy[denied_id] = policy

    return domains, routes, expected_counts, deny_policy


def _routes_for_view(
    routes: Mapping[str, Mapping[str, Any]],
    view: str,
    expected_counts: Mapping[str, int],
) -> list[tuple[str, Mapping[str, Any]]]:
    selected = [
        (command_id, route)
        for command_id, route in routes.items()
        if route["view"] == view
    ]
    expected = expected_counts.get(view)
    if expected is None:
        raise DiscoveryError(f"unknown discovery view: {view}")
    if len(selected) != expected:
        raise DiscoveryError(
            f"routing overlay expected {expected} {view} commands, "
            f"found {len(selected)}"
        )
    return selected


def _domain_index(
    *,
    view: str,
    domains: Mapping[str, Mapping[str, Any]],
    view_routes: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    domain_items: list[dict[str, Any]] = []
    for domain_id, domain in domains.items():
        if domain["view"] != view:
            continue
        matching = [
            route
            for _, route in view_routes
            if domain_id in route["domains"]
        ]
        if not matching:
            continue
        tier_counts: dict[str, int] = {}
        for route in matching:
            tier = route["tier"]
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        tiers = {
            tier: tier_counts[tier]
            for tier in ("core", "advanced", "control-plane", "custom")
            if tier in tier_counts
        }
        domain_items.append(
            {
                "id": domain_id,
                "summary": domain["summary"],
                "tiers": tiers,
            }
        )
    return {
        "schemaVersion": 1,
        "kind": "domain-index",
        "view": view,
        "totalCommands": len(view_routes),
        "domains": domain_items,
    }


def _route_cards(
    *,
    requested_domains: Sequence[str],
    tier: str,
    view: str,
    domains: Mapping[str, Mapping[str, Any]],
    view_routes: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    available_domains = {
        domain_id
        for domain_id, domain in domains.items()
        if domain["view"] == view
    }
    for domain_id in requested_domains:
        if domain_id not in available_domains:
            raise DiscoveryError(f"unknown domain for {view} view: {domain_id}")

    cards: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for domain_id in requested_domains:
        for command_id, route in view_routes:
            if (
                command_id in emitted
                or domain_id not in route["domains"]
                or route["tier"] != tier
            ):
                continue
            emitted.add(command_id)
            cards.append(
                {
                    "id": command_id,
                    "domains": list(route["domains"]),
                    "selectWhen": route["selectWhen"],
                    "avoidWhen": route["avoidWhen"],
                    "effect": route["effect"],
                }
            )

    return {
        "schemaVersion": 1,
        "kind": "route-cards",
        "view": view,
        "domains": list(requested_domains),
        "tier": tier,
        "routes": cards,
    }


def _contract_item(
    command_id: str,
    *,
    contracts: Mapping[str, Mapping[str, Any]],
    routes: Mapping[str, Mapping[str, Any]],
    contract_detail: str,
) -> dict[str, Any]:
    route = routes[command_id]
    return {
        "contract": _project_contract(
            contracts[command_id],
            contract_detail,
        ),
        "domains": list(route["domains"]),
        "tier": route["tier"],
        "effect": route["effect"],
        "limitations": list(route["limitations"]),
    }


def _deny_item(policy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": policy["id"],
        "domains": list(policy["domains"]),
        "tier": policy["tier"],
        "intent": policy["intent"],
        "reason": policy["reason"],
        "fallbackPolicy": policy["fallbackPolicy"],
        "invoke": False,
    }


def _contract_bundle(
    *,
    command_ids: Sequence[str],
    view: str,
    routes: Mapping[str, Mapping[str, Any]],
    contracts: Mapping[str, Mapping[str, Any]],
    deny_policy: Mapping[str, Mapping[str, Any]],
    contract_detail: str,
) -> dict[str, Any]:
    selected_ids: list[str] = []
    denied_ids: list[str] = []
    for command_id in command_ids:
        route = routes.get(command_id)
        if route is None or route["view"] != view:
            denied = deny_policy.get(command_id)
            if denied is not None:
                denied_ids.append(command_id)
                continue
            raise DiscoveryError(
                f"unknown command for {view} view: {command_id}",
                code="unknown_command",
            )
        selected_ids.append(command_id)

    selected_set = set(selected_ids)
    related_ids: list[str] = []
    related_seen: set[str] = set()
    relations: list[dict[str, Any]] = []
    for command_id in selected_ids:
        route = routes[command_id]
        prepare_with = list(route["prepareWith"])
        verify_with = list(route["verifyWith"])
        relations.append(
            {
                "source": command_id,
                "prepareWith": prepare_with,
                "verifyWith": verify_with,
            }
        )
        for related_id in prepare_with + verify_with:
            if related_id in selected_set or related_id in related_seen:
                continue
            related_route = routes[related_id]
            if related_route["view"] != view:
                raise DiscoveryError(
                    f"route {command_id} relates across discovery views: "
                    f"{related_id}"
                )
            related_seen.add(related_id)
            related_ids.append(related_id)

    result = {
        "schemaVersion": 1,
        "kind": "contract-bundle",
        "view": view,
        "contractEncoding": (
            "canonical-agent-v2"
            if contract_detail == "compact"
            else "package-v1"
        ),
        "selected": [
            _contract_item(
                command_id,
                contracts=contracts,
                routes=routes,
                contract_detail=contract_detail,
            )
            for command_id in selected_ids
        ],
        "related": [
            _contract_item(
                command_id,
                contracts=contracts,
                routes=routes,
                contract_detail=contract_detail,
            )
            for command_id in related_ids
        ],
        "relations": relations,
    }
    if denied_ids:
        result["denied"] = [
            _deny_item(deny_policy[command_id])
            for command_id in denied_ids
        ]
    return result


def _custom_discovery(
    *,
    requested_domains: Sequence[str],
    requested_ids: Sequence[str],
    tier: str | None,
    contracts: Mapping[str, Mapping[str, Any]],
    partitions: Mapping[str, str],
    contract_detail: str,
    deny_policy: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    custom_ids = [
        command_id
        for command_id in contracts
        if (
            partitions[command_id] == "custom"
            and command_id not in deny_policy
        )
    ]

    if requested_domains:
        for domain_id in requested_domains:
            if domain_id != "custom":
                raise DiscoveryError(
                    f"unknown domain for custom view: {domain_id}"
                )
        selected_tier = tier or "custom"
        if selected_tier != "custom":
            raise DiscoveryError(
                "custom discovery supports only the custom tier"
            )
        return {
            "schemaVersion": 1,
            "kind": "route-cards",
            "view": "custom",
            "domains": list(requested_domains),
            "tier": "custom",
            "routes": [
                {
                    "id": command_id,
                    "domains": ["custom"],
                    "selectWhen": contracts[command_id].get("summary", ""),
                    "avoidWhen": "",
                    "effect": "project-defined",
                }
                for command_id in custom_ids
            ],
        }

    if requested_ids:
        selected_ids: list[str] = []
        denied_ids: list[str] = []
        for command_id in requested_ids:
            denied = deny_policy.get(command_id)
            if denied is not None:
                denied_ids.append(command_id)
                continue
            if partitions.get(command_id) != "custom":
                raise DiscoveryError(
                    f"unknown command for custom view: {command_id}",
                    code="unknown_command",
                )
            selected_ids.append(command_id)
        result = {
            "schemaVersion": 1,
            "kind": "contract-bundle",
            "view": "custom",
            "contractEncoding": (
                "canonical-agent-v2"
                if contract_detail == "compact"
                else "package-v1"
            ),
            "selected": [
                {
                    "contract": _project_contract(
                        contracts[command_id],
                        contract_detail,
                    ),
                    "domains": ["custom"],
                    "tier": "custom",
                    "effect": "project-defined",
                    "limitations": [],
                }
                for command_id in selected_ids
            ],
            "related": [],
            "relations": [],
        }
        if denied_ids:
            result["denied"] = [
                _deny_item(deny_policy[command_id])
                for command_id in denied_ids
            ]
        return result

    domain_items = []
    if custom_ids:
        domain_items.append(
            {
                "id": "custom",
                "summary": "Project-defined package command contracts.",
                "tiers": {"custom": len(custom_ids)},
            }
        )
    return {
        "schemaVersion": 1,
        "kind": "domain-index",
        "view": "custom",
        "totalCommands": len(custom_ids),
        "domains": domain_items,
    }


def discover(
    snapshot: Mapping[str, Any],
    *,
    domains: Sequence[str] | None = None,
    command_ids: Sequence[str] | None = None,
    tier: str | None = None,
    view: str = "authoring",
    overlay: Mapping[str, Any] | None = None,
    contract_detail: str = "compact",
) -> dict[str, Any]:
    """Project a package registry snapshot into one bounded discovery response.

    ``contract_detail`` controls only Contract Bundles. Compact detail is the
    execution-complete agent encoding with a projected result inventory;
    package detail retains the normalized registry shape for diagnostics.
    """

    if domains and command_ids:
        raise DiscoveryError(
            "domain and command-id selectors are mutually exclusive"
        )
    if command_ids and tier is not None:
        raise DiscoveryError("tier is only valid with domain selectors")
    if not domains and not command_ids and tier is not None:
        raise DiscoveryError("tier is only valid with domain selectors")
    if not isinstance(snapshot, Mapping):
        raise DiscoveryError("registry snapshot must be an object")
    if not isinstance(view, str) or not view:
        raise DiscoveryError("view must be a non-empty string")
    if contract_detail not in ("compact", "package"):
        raise DiscoveryError(
            "contract_detail must be 'compact' or 'package'"
        )

    requested_domains = _deduplicate(domains or ())
    requested_ids = _deduplicate(command_ids or ())
    contracts, partitions = _contracts_by_id(snapshot)
    selected_overlay = overlay if overlay is not None else _load_default_overlay()
    if not isinstance(selected_overlay, Mapping):
        raise DiscoveryError("routing overlay must be an object")
    overlay_domains, routes, expected_counts, deny_policy = _validated_overlay(
        selected_overlay,
        contracts,
    )
    if view == "custom":
        return _custom_discovery(
            requested_domains=requested_domains,
            requested_ids=requested_ids,
            tier=tier,
            contracts=contracts,
            partitions=partitions,
            contract_detail=contract_detail,
            deny_policy=deny_policy,
        )
    view_routes = _routes_for_view(routes, view, expected_counts)

    if requested_domains:
        allowed_tiers = (
            {"control-plane"}
            if view == "control"
            else {"core", "advanced"}
        )
        selected_tier = tier or (
            "control-plane" if view == "control" else "core"
        )
        if selected_tier not in allowed_tiers:
            allowed = ", ".join(sorted(allowed_tiers))
            raise DiscoveryError(
                f"{view} discovery supports these tiers: {allowed}"
            )
        return _route_cards(
            requested_domains=requested_domains,
            tier=selected_tier,
            view=view,
            domains=overlay_domains,
            view_routes=view_routes,
        )
    if requested_ids:
        return _contract_bundle(
            command_ids=requested_ids,
            view=view,
            routes=routes,
            contracts=contracts,
            deny_policy=deny_policy,
            contract_detail=contract_detail,
        )
    return _domain_index(
        view=view,
        domains=overlay_domains,
        view_routes=view_routes,
    )
