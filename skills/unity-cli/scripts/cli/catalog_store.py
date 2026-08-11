"""Strict, deterministic storage for the shared custom-command catalog.

The package registry remains the command-contract authority.  The catalog stores
one exact copy of the package-owned custom partition so a team can share its
project-specific command surface without inventing a second contract schema.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

from cli.paths import atomic_write
from cli.registry_protocol import (
    RegistryProtocolError,
    SCHEMA_VERSION,
    compute_partition_fingerprint,
    validate_fingerprint,
)


CATALOG_VERSION = 2
WRITE_WRITTEN = "written"
WRITE_UNCHANGED = "unchanged"
WRITE_CONFLICT = "conflict"
WRITE_FAILED = "failed"

_CATALOG_FIELDS = frozenset({
    "catalogVersion",
    "registrySchemaVersion",
    "customFingerprint",
    "commands",
})


class CatalogStoreError(ValueError):
    """The catalog cannot be trusted or safely accessed."""


@dataclass(frozen=True)
class CatalogReadState:
    """The bytes observed before registry resolution and any trusted payload."""

    digest: str | None
    catalog: dict | None
    invalid_reason: str = ""


def _canonical_text(catalog):
    return json.dumps(
        catalog,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def _strict_json_loads(text):
    def object_from_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CatalogStoreError(
                    f"duplicate JSON field in catalog: {key!r}"
                )
            result[key] = value
        return result

    def reject_constant(value):
        raise CatalogStoreError(
            f"non-finite JSON number in catalog: {value}"
        )

    return json.loads(
        text,
        object_pairs_hook=object_from_pairs,
        parse_constant=reject_constant,
    )


def validate_catalog(catalog):
    """Validate strict catalog v2 and its package-compatible fingerprint."""
    prefix = "catalog schema v2"
    if not isinstance(catalog, dict):
        raise CatalogStoreError(f"{prefix} must be a JSON object")
    if (
        type(catalog.get("catalogVersion")) is not int
        or catalog["catalogVersion"] != CATALOG_VERSION
    ):
        raise CatalogStoreError(
            f"unsupported catalog; {prefix} is required—run 'cs catalog sync'"
        )
    if set(catalog) != _CATALOG_FIELDS:
        raise CatalogStoreError(
            f"{prefix} must contain only the canonical fields"
        )
    if (
        type(catalog["registrySchemaVersion"]) is not int
        or catalog["registrySchemaVersion"] != SCHEMA_VERSION
    ):
        raise CatalogStoreError(
            f"{prefix} has an unsupported registry schema version"
        )
    if not isinstance(catalog["customFingerprint"], str):
        raise CatalogStoreError(
            f"{prefix} customFingerprint must be a string"
        )

    commands = catalog["commands"]
    try:
        expected = validate_fingerprint(catalog["customFingerprint"])
        actual = compute_partition_fingerprint("custom", commands)
    except (
        RegistryProtocolError,
        TypeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise CatalogStoreError(f"invalid {prefix}: {exc}") from exc
    if not hmac.compare_digest(expected, actual):
        raise CatalogStoreError(
            f"invalid {prefix}: custom fingerprint does not match commands"
        )
    return catalog


def build_catalog(commands, custom_fingerprint):
    """Build and validate one deterministic catalog document."""
    catalog = {
        "catalogVersion": CATALOG_VERSION,
        "registrySchemaVersion": SCHEMA_VERSION,
        "customFingerprint": custom_fingerprint,
        "commands": commands,
    }
    return validate_catalog(catalog)


def render_catalog(catalog):
    """Return canonical UTF-8/LF JSON text for a valid catalog."""
    validate_catalog(catalog)
    try:
        return _canonical_text(catalog)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CatalogStoreError(
            f"cannot serialize catalog schema v2: {exc}"
        ) from exc


def _digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_bytes(path):
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CatalogStoreError(f"cannot read catalog: {exc}") from exc


def read_catalog_state(path):
    """Read target bytes once before live work; preserve invalid-file identity."""
    raw = _read_bytes(path)
    if raw is None:
        return CatalogReadState(digest=None, catalog=None)
    digest = _digest_bytes(raw)
    try:
        text = raw.decode("utf-8")
        catalog = validate_catalog(_strict_json_loads(text))
        return CatalogReadState(digest=digest, catalog=catalog)
    except (
        UnicodeError,
        json.JSONDecodeError,
        CatalogStoreError,
        TypeError,
        ValueError,
        RecursionError,
    ) as exc:
        return CatalogReadState(
            digest=digest,
            catalog=None,
            invalid_reason=str(exc),
        )


def load_catalog(path):
    """Load a trusted v2 catalog or raise a concise user-facing error."""
    state = read_catalog_state(path)
    if state.digest is None:
        raise CatalogStoreError(f"catalog does not exist: {Path(path)}")
    if state.catalog is None:
        raise CatalogStoreError(
            state.invalid_reason or "invalid catalog schema v2"
        )
    return state.catalog


def _current_digest(path):
    raw = _read_bytes(path)
    return _digest_bytes(raw) if raw is not None else None


def _valid_expected_digest(value):
    return (
        value is None
        or (
            isinstance(value, str)
            and len(value) == 64
            and value == value.lower()
            and all(character in "0123456789abcdef" for character in value)
        )
    )


def save_catalog(path, text, *, expected_digest=None):
    """Compare-and-swap one valid catalog into place."""
    if not _valid_expected_digest(expected_digest):
        return WRITE_FAILED

    try:
        candidate = validate_catalog(_strict_json_loads(text))
        if text != _canonical_text(candidate):
            return WRITE_FAILED
        encoded = text.encode("utf-8")
    except (
        UnicodeError,
        json.JSONDecodeError,
        CatalogStoreError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        return WRITE_FAILED

    target = Path(path)
    try:
        observed = _current_digest(target)
        if observed == _digest_bytes(encoded):
            return WRITE_UNCHANGED
        if observed != expected_digest:
            return WRITE_CONFLICT
        if atomic_write(target, text):
            return WRITE_WRITTEN
        return WRITE_FAILED
    except CatalogStoreError:
        return WRITE_FAILED
