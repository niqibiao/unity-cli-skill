"""Resolve package-owned command contracts with one conditional request.

The Unity package is the executable contract authority.  This module sends the
cached registry generation token with one conditional snapshot request and
coordinates the answer with a per-project, machine-local cache.  The cache is
initialized by the first successful live resolution; there is no bundled
offline artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cli.registry_cache import (
    load_registry_cache,
    save_registry_cache,
)
from cli.registry_protocol import (
    RegistryProtocolError,
    validate_snapshot,
    validate_unchanged_response,
)
from cli.paths import registry_cache_path


_PARTITIONS = ("builtin", "custom")

_NO_REGISTRY_GUIDANCE = (
    "no valid command registry cache for this project; start the Unity "
    "editor service once to initialize the command registry"
)


class RegistryResolutionError(RuntimeError):
    """No valid live or cached registry could be resolved."""


class RegistryLiveError(RegistryResolutionError):
    """The package registry control plane returned an unusable response."""


@dataclass(frozen=True)
class ResolvedRegistry:
    """One immutable resolution decision and its source metadata."""

    snapshot: dict
    source: str
    custom_available: bool
    live_checked: bool
    cache_stored: bool
    stale_reason: str = ""


def _extract_payload(envelope, operation):
    if not isinstance(envelope, dict):
        raise RegistryLiveError(f"{operation} returned a non-object envelope")
    if envelope.get("ok") is not True:
        summary = envelope.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            summary = "request failed"
        raise RegistryLiveError(f"{operation} failed: {summary}")

    data = envelope.get("data")
    if not isinstance(data, dict) or "resultJson" not in data:
        raise RegistryLiveError(f"{operation} response is missing data.resultJson")
    payload = data["resultJson"]
    if not isinstance(payload, dict):
        raise RegistryLiveError(f"{operation} result must be a JSON object")
    return payload


class RegistryResolver:
    """Resolve a command registry once for one CLI discovery invocation.

    Repeated normal calls on this object return the memoized decision.  An explicit
    refresh deliberately starts a new live cycle that omits the cached token, so
    the package always answers with the full current snapshot.
    """

    def __init__(
        self,
        project_root,
        *,
        session=None,
        cache_path=None,
    ):
        self.project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else None
        )
        self.session = session
        if cache_path is not None:
            self.cache_path = Path(cache_path)
        elif self.project_root is not None:
            self.cache_path = registry_cache_path(self.project_root)
        else:
            self.cache_path = None
        self._online_resolution = None
        self._offline_resolution = None

    def resolve(self, *, offline=False, refresh=False):
        if offline:
            if not refresh and self._offline_resolution is not None:
                return self._offline_resolution
            resolution = self._resolve_offline()
            self._offline_resolution = resolution
            return resolution

        if not refresh and self._online_resolution is not None:
            return self._online_resolution

        cached = self._load_cache()
        try:
            resolution = self._resolve_live(cached, refresh=refresh)
        except (RegistryLiveError, RegistryProtocolError, OSError) as exc:
            resolution = self._recover_without_live(cached, str(exc))

        self._online_resolution = resolution
        return resolution

    def _resolve_offline(self):
        cached = self._load_cache()
        if cached is not None:
            return ResolvedRegistry(
                snapshot=cached,
                source="cache",
                custom_available=True,
                live_checked=False,
                cache_stored=True,
            )
        raise RegistryResolutionError(_NO_REGISTRY_GUIDANCE)

    def _resolve_live(self, cached, *, refresh):
        if self.session is None:
            raise RegistryLiveError("live registry resolution needs a session")

        token = None
        if not refresh and cached is not None:
            token = cached["registryGeneration"]

        raw_snapshot = _extract_payload(
            self.session.registry_snapshot(token),
            "registry snapshot",
        )

        if raw_snapshot.get("unchanged") is True:
            unchanged = validate_unchanged_response(raw_snapshot)
            if token is None or unchanged["registryGeneration"] != token:
                raise RegistryLiveError(
                    "unchanged registry answer does not match the cached generation"
                )
            return ResolvedRegistry(
                snapshot=cached,
                source="cache",
                custom_available=True,
                live_checked=True,
                cache_stored=True,
            )

        candidate = validate_snapshot(
            raw_snapshot,
            required_included=set(_PARTITIONS),
        )
        starting_generation = (
            cached["registryGeneration"] if cached is not None else None
        )
        stored = (
            save_registry_cache(
                self.cache_path,
                candidate,
                expected_generation=starting_generation,
            )
            if self.cache_path is not None
            else False
        )
        return ResolvedRegistry(
            snapshot=candidate,
            source="live",
            custom_available=True,
            live_checked=True,
            cache_stored=stored,
        )

    def _recover_without_live(self, cached, reason):
        if cached is not None:
            return ResolvedRegistry(
                snapshot=cached,
                source="stale-cache",
                custom_available=True,
                live_checked=True,
                cache_stored=True,
                stale_reason=reason,
            )
        raise RegistryResolutionError(f"{_NO_REGISTRY_GUIDANCE}: {reason}")

    def _load_cache(self):
        if self.cache_path is None:
            return None
        return load_registry_cache(self.cache_path)
