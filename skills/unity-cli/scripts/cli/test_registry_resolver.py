"""Local-only TDD coverage for package-owned registry resolution.

This file is intentionally untracked and must not be committed.
"""

import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CLI_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli import paths  # noqa: E402
from cli.registry_cache import (  # noqa: E402
    load_registry_cache,
    save_registry_cache,
)
from cli.registry_protocol import compute_partition_fingerprint  # noqa: E402
from cli.registry_resolver import (  # noqa: E402
    RegistryResolutionError,
    RegistryResolver,
)


def _generation(builtin_count, builtin_fingerprint, custom_count, custom_fingerprint):
    def wire_string(value):
        data = value.encode("utf-8")
        return struct.pack("<i", len(data)) + data

    payload = (
        struct.pack("<i", 1)
        + struct.pack("<i", builtin_count)
        + wire_string(builtin_fingerprint)
        + struct.pack("<i", custom_count)
        + wire_string(custom_fingerprint)
    )
    return hashlib.sha256(payload).hexdigest()


def _command(command_id, partition, summary=None):
    namespace, action = command_id.split("/", 1)
    return {
        "id": command_id,
        "wire": {
            "commandNamespace": namespace,
            "action": action,
        },
        "summary": summary or command_id,
        "partition": partition,
        "requirements": {
            "editor": True,
            "mainThread": True,
            "sessionId": False,
        },
        "arguments": [],
        "result": {
            "kind": "empty",
            "format": "",
            "nullable": False,
            "enumValues": [],
            "fields": [],
        },
        "rules": [],
    }


def _state(builtin_ids, custom_ids=(), revision="a"):
    builtin = [
        _command(item, "builtin", f"{item}:{revision}")
        for item in sorted(builtin_ids)
    ]
    custom = [
        _command(item, "custom", f"{item}:{revision}")
        for item in sorted(custom_ids)
    ]
    builtin_fingerprint = compute_partition_fingerprint("builtin", builtin)
    custom_fingerprint = compute_partition_fingerprint("custom", custom)
    generation = _generation(
        len(builtin),
        builtin_fingerprint,
        len(custom),
        custom_fingerprint,
    )
    return {
        "schemaVersion": 1,
        "registryGeneration": generation,
        "builtin": {
            "included": True,
            "count": len(builtin),
            "fingerprint": builtin_fingerprint,
            "commands": builtin,
        },
        "custom": {
            "included": True,
            "count": len(custom),
            "fingerprint": custom_fingerprint,
            "commands": custom,
        },
    }


def _unchanged(generation):
    return {
        "schemaVersion": 1,
        "registryGeneration": generation,
        "unchanged": True,
    }


def _envelope(payload):
    return {
        "ok": True,
        "exitCode": 0,
        "data": {"resultJson": payload},
    }


class _FakeSession:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.snapshot_calls = []

    def registry_snapshot(self, if_generation=None):
        self.snapshot_calls.append(if_generation)
        if not self.responses:
            raise AssertionError("unexpected registry snapshot request")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict) and "ok" in value:
            return value
        return _envelope(value)


class RegistryResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "Project"
        self.project.mkdir()
        self.cache_path = root / "cache" / "command-registry.v1.json"

    def _resolver(self, session=None):
        return RegistryResolver(
            self.project,
            session=session,
            cache_path=self.cache_path,
        )

    def test_double_encoded_snapshot_is_not_reparsed(self):
        snapshot = _state(["editor/status"])
        session = mock.Mock()
        session.registry_snapshot.return_value = {
            "ok": True,
            "data": {
                "resultJson": json.dumps(
                    snapshot,
                    separators=(",", ":"),
                )
            },
        }

        with self.assertRaises(RegistryResolutionError):
            self._resolver(session).resolve()

    def test_cache_miss_fetches_full_snapshot_once_and_memoizes(self):
        live = _state(["editor/status"], ["studio/ping"])
        session = _FakeSession([live])
        resolver = self._resolver(session)

        first = resolver.resolve()
        second = resolver.resolve()

        self.assertIs(first, second)
        self.assertEqual("live", first.source)
        self.assertTrue(first.custom_available)
        self.assertTrue(first.live_checked)
        self.assertEqual([None], session.snapshot_calls)
        self.assertEqual(live, load_registry_cache(self.cache_path))

    def test_matching_cache_sends_token_and_transfers_no_contracts(self):
        cached = _state(["editor/status"], ["studio/ping"])
        self.assertTrue(save_registry_cache(self.cache_path, cached))
        session = _FakeSession([_unchanged(cached["registryGeneration"])])

        resolved = self._resolver(session).resolve()

        self.assertEqual("cache", resolved.source)
        self.assertTrue(resolved.live_checked)
        self.assertEqual(
            [cached["registryGeneration"]],
            session.snapshot_calls,
        )
        self.assertEqual(cached, resolved.snapshot)

    def test_changed_registry_replaces_cache_with_full_snapshot(self):
        cached = _state(["editor/status"], revision="old")
        current = _state(
            ["editor/status", "gameobject/get"],
            ["studio/ping"],
            revision="new",
        )
        self.assertTrue(save_registry_cache(self.cache_path, cached))
        session = _FakeSession([current])

        resolved = self._resolver(session).resolve()

        self.assertEqual("live", resolved.source)
        self.assertEqual(
            [cached["registryGeneration"]],
            session.snapshot_calls,
        )
        self.assertEqual(current, resolved.snapshot)
        self.assertEqual(current, load_registry_cache(self.cache_path))

    def test_explicit_refresh_omits_token_even_with_valid_cache(self):
        cached = _state(["editor/status"])
        self.assertTrue(save_registry_cache(self.cache_path, cached))
        session = _FakeSession([cached])

        resolved = self._resolver(session).resolve(refresh=True)

        self.assertEqual("live", resolved.source)
        self.assertEqual([None], session.snapshot_calls)

    def test_unchanged_answer_with_wrong_generation_falls_back_to_stale_cache(self):
        cached = _state(["editor/status"], revision="cached")
        other = _state(["editor/status", "gameobject/get"], revision="other")
        self.assertTrue(save_registry_cache(self.cache_path, cached))
        session = _FakeSession([_unchanged(other["registryGeneration"])])

        resolved = self._resolver(session).resolve()

        self.assertEqual("stale-cache", resolved.source)
        self.assertTrue(resolved.stale_reason)
        self.assertEqual(cached, resolved.snapshot)

    def test_unchanged_answer_without_cache_fails_with_guidance(self):
        state = _state(["editor/status"])
        session = _FakeSession([_unchanged(state["registryGeneration"])])

        with self.assertRaisesRegex(
            RegistryResolutionError,
            "start the Unity editor service once",
        ):
            self._resolver(session).resolve()

    def test_bad_live_candidate_preserves_cache_and_returns_stale_cache(self):
        cached = _state(["editor/status"], revision="cached")
        self.assertTrue(save_registry_cache(self.cache_path, cached))
        original_bytes = self.cache_path.read_bytes()
        session = _FakeSession(
            [{"ok": True, "data": {"resultJson": "{not-json"}}],
        )

        resolved = self._resolver(session).resolve()

        self.assertEqual("stale-cache", resolved.source)
        self.assertTrue(resolved.stale_reason)
        self.assertEqual(cached, resolved.snapshot)
        self.assertEqual(original_bytes, self.cache_path.read_bytes())

    def test_offline_prefers_valid_cache_and_keeps_custom_available(self):
        cached = _state(["editor/status"], ["studio/ping"])
        self.assertTrue(save_registry_cache(self.cache_path, cached))

        resolved = self._resolver().resolve(offline=True)

        self.assertEqual("cache", resolved.source)
        self.assertFalse(resolved.live_checked)
        self.assertTrue(resolved.custom_available)
        self.assertEqual(cached, resolved.snapshot)

    def test_offline_corrupt_cache_fails_with_guidance(self):
        self.cache_path.parent.mkdir(parents=True)
        self.cache_path.write_text("{broken", "utf-8")

        with self.assertRaisesRegex(
            RegistryResolutionError,
            "start the Unity editor service once",
        ):
            self._resolver().resolve(offline=True)

    def test_cache_with_broken_structure_is_discarded(self):
        cached = _state(["editor/status"])
        self.assertTrue(save_registry_cache(self.cache_path, cached))
        wrapper = json.loads(self.cache_path.read_text("utf-8"))
        del wrapper["snapshot"]["builtin"]["commands"][0]["summary"]
        self.cache_path.write_text(json.dumps(wrapper), "utf-8")

        self.assertIsNone(load_registry_cache(self.cache_path))

    def test_failed_cache_replace_returns_live_but_preserves_previous_cache(self):
        cached = _state(["editor/status"], revision="cached")
        current = _state(
            ["editor/status", "gameobject/get"],
            revision="current",
        )
        self.assertTrue(save_registry_cache(self.cache_path, cached))
        original_bytes = self.cache_path.read_bytes()
        session = _FakeSession([current])

        with mock.patch.object(paths.os, "replace", side_effect=OSError("blocked")):
            resolved = self._resolver(session).resolve()

        self.assertEqual("live", resolved.source)
        self.assertFalse(resolved.cache_stored)
        self.assertEqual(current["registryGeneration"], resolved.snapshot["registryGeneration"])
        self.assertEqual(original_bytes, self.cache_path.read_bytes())
        offline = self._resolver().resolve(offline=True)
        self.assertEqual("cache", offline.source)
        self.assertEqual(cached, offline.snapshot)

    def test_offline_without_any_cache_fails_closed(self):
        with self.assertRaises(RegistryResolutionError):
            self._resolver().resolve(offline=True)


class RegistryCachePathTests(unittest.TestCase):
    def test_registry_cache_is_keyed_per_project(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(paths, "cache_root", return_value=Path(temp) / "cache"):
                first = paths.registry_cache_path(Path(temp) / "ProjectA")
                second = paths.registry_cache_path(Path(temp) / "ProjectB")

        self.assertNotEqual(first, second)
        self.assertEqual("command-registry.v1.json", first.name)

    def test_atomic_write_failure_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "state.json"
            target.write_text("old", "utf-8")

            with mock.patch.object(paths.os, "replace", side_effect=OSError("blocked")):
                written = paths.atomic_write(target, "new")

            self.assertFalse(written)
            self.assertEqual("old", target.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
