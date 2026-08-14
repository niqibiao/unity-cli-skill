"""Local-only TDD coverage for the package-owned custom-command catalog."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CLI_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

CLI = CLI_DIR / "cs.py"
SPEC = importlib.util.spec_from_file_location("unity_cli_catalog_v2", CLI)
CS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CS)

from cli import catalog_store, core_bridge, paths, registry_resolver  # noqa: E402
from cli.registry_protocol import compute_partition_fingerprint  # noqa: E402


def _custom_contract():
    return {
        "id": "teamtools/build_room",
        "wire": {
            "commandNamespace": "internal-teamtools",
            "action": "build-room-v3",
        },
        "summary": "Build a room from the project convention.",
        "partition": "custom",
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
                "required": True,
                "hasDefault": False,
                "defaultJson": "",
                "nonEmpty": True,
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


def _resolution(*, source="cache", live_checked=True):
    command = _custom_contract()
    custom_fingerprint = compute_partition_fingerprint("custom", [command])
    return SimpleNamespace(
        source=source,
        live_checked=live_checked,
        cache_stored=True,
        custom_available=True,
        stale_reason="connection refused" if source == "stale-cache" else "",
        snapshot={
            "schemaVersion": 1,
            "registryGeneration": "registry-generation",
            "builtin": {
                "included": True,
                "count": 0,
                "fingerprint": "builtin-fingerprint",
                "commands": [],
            },
            "custom": {
                "included": True,
                "count": 1,
                "fingerprint": custom_fingerprint,
                "commands": [command],
            },
        },
    )


class _Resolver:
    resolution = _resolution()
    instances = []

    def __init__(self, root, *, session=None):
        self.root = root
        self.session = session
        self.calls = 0
        type(self).instances.append(self)

    def resolve(self):
        self.calls += 1
        return type(self).resolution


class CatalogV2Tests(unittest.TestCase):
    def setUp(self):
        _Resolver.instances = []
        _Resolver.resolution = _resolution()

    def test_sync_writes_canonical_v2_catalog_without_wire_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog_path = root / "catalog.json"
            args = SimpleNamespace(
                catalog_path=str(catalog_path),
                as_json=True,
            )
            stdout = io.StringIO()

            with (
                mock.patch.object(core_bridge, "find_package_dir", return_value=root),
                mock.patch.object(CS, "_new_session", return_value=object()),
                mock.patch.object(
                    registry_resolver,
                    "RegistryResolver",
                    _Resolver,
                ),
                mock.patch.object(
                    paths,
                    "cache_root",
                    return_value=root / "cache",
                ),
                contextlib.redirect_stdout(stdout),
            ):
                status = CS.cmd_catalog_sync(root, args, root)

            self.assertEqual(0, status)
            self.assertEqual(1, len(_Resolver.instances))
            self.assertEqual(1, _Resolver.instances[0].calls)
            catalog = json.loads(catalog_path.read_text("utf-8"))
            self.assertEqual(
                {
                    "catalogVersion",
                    "registrySchemaVersion",
                    "customFingerprint",
                    "commands",
                },
                set(catalog),
            )
            self.assertEqual(2, catalog["catalogVersion"])
            self.assertEqual(1, catalog["registrySchemaVersion"])
            self.assertEqual(
                compute_partition_fingerprint(
                    "custom",
                    [_custom_contract()],
                ),
                catalog["customFingerprint"],
            )
            self.assertEqual(_custom_contract(), catalog["commands"][0])
            self.assertEqual(
                "teamtools/build_room",
                catalog["commands"][0]["id"],
            )
            self.assertNotIn("namespace", catalog["commands"][0])
            self.assertNotIn("args", catalog["commands"][0])

            result = json.loads(stdout.getvalue())
            self.assertTrue(result["ok"])
            self.assertEqual(["teamtools/build_room"], result["data"]["added"])
            self.assertEqual([], result["data"]["removed"])

    def test_sync_refuses_unverified_stale_registry_and_preserves_catalog(self):
        _Resolver.resolution = _resolution(source="stale-cache")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog_path = root / "catalog.json"
            original = '{"doNotOverwrite":true}\n'
            catalog_path.write_text(original, "utf-8")
            args = SimpleNamespace(
                catalog_path=str(catalog_path),
                as_json=True,
            )
            stdout = io.StringIO()

            with (
                mock.patch.object(core_bridge, "find_package_dir", return_value=root),
                mock.patch.object(CS, "_new_session", return_value=object()),
                mock.patch.object(
                    registry_resolver,
                    "RegistryResolver",
                    _Resolver,
                ),
                mock.patch.object(
                    paths,
                    "cache_root",
                    return_value=root / "cache",
                ),
                contextlib.redirect_stdout(stdout),
            ):
                status = CS.cmd_catalog_sync(root, args, root)

            self.assertEqual(1, status)
            self.assertEqual(original, catalog_path.read_text("utf-8"))
            result = json.loads(stdout.getvalue())
            self.assertFalse(result["ok"])
            self.assertIn("could not be verified", result["summary"])

    def test_sync_reports_atomic_write_failure_and_preserves_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog_path = root / "catalog.json"
            original = '{"doNotOverwrite":true}\n'
            catalog_path.write_text(original, "utf-8")
            args = SimpleNamespace(
                catalog_path=str(catalog_path),
                as_json=True,
            )
            stdout = io.StringIO()

            with (
                mock.patch.object(core_bridge, "find_package_dir", return_value=root),
                mock.patch.object(CS, "_new_session", return_value=object()),
                mock.patch.object(
                    registry_resolver,
                    "RegistryResolver",
                    _Resolver,
                ),
                mock.patch.object(
                    paths,
                    "cache_root",
                    return_value=root / "cache",
                ),
                mock.patch.object(
                    catalog_store,
                    "atomic_write",
                    return_value=False,
                ),
                contextlib.redirect_stdout(stdout),
            ):
                status = CS.cmd_catalog_sync(root, args, root)

            self.assertEqual(1, status)
            self.assertEqual(original, catalog_path.read_text("utf-8"))
            result = json.loads(stdout.getvalue())
            self.assertFalse(result["ok"])
            self.assertIn("preserved", result["summary"])

    def test_sync_accepts_verified_empty_custom_partition(self):
        empty = _resolution(source="live")
        empty.snapshot["custom"] = {
            "included": True,
            "count": 0,
            "fingerprint": compute_partition_fingerprint("custom", []),
            "commands": [],
        }
        _Resolver.resolution = empty

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog_path = root / "catalog.json"
            args = SimpleNamespace(
                catalog_path=str(catalog_path),
                as_json=True,
            )
            stdout = io.StringIO()

            with (
                mock.patch.object(core_bridge, "find_package_dir", return_value=root),
                mock.patch.object(CS, "_new_session", return_value=object()),
                mock.patch.object(
                    registry_resolver,
                    "RegistryResolver",
                    _Resolver,
                ),
                mock.patch.object(
                    paths,
                    "cache_root",
                    return_value=root / "cache",
                ),
                contextlib.redirect_stdout(stdout),
            ):
                status = CS.cmd_catalog_sync(root, args, root)

            self.assertEqual(0, status)
            catalog = catalog_store.load_catalog(catalog_path)
            self.assertEqual([], catalog["commands"])
            result = json.loads(stdout.getvalue())
            self.assertEqual(0, result["data"]["total"])
            self.assertEqual("live", result["data"]["source"])

    def test_list_rejects_legacy_v1_catalog_instead_of_compatibility_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog_path = root / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "discovered_at": "then",
                        "commands": [],
                    }
                ),
                "utf-8",
            )
            args = SimpleNamespace(
                catalog_path=str(catalog_path),
                as_json=True,
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                status = CS.cmd_catalog_list(root, args)

            self.assertEqual(1, status)
            result = json.loads(stdout.getvalue())
            self.assertFalse(result["ok"])
            self.assertIn("schema v2", result["summary"])

    def test_store_skips_identical_rewrite_and_detects_cas_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog_path = root / "catalog.json"
            command = _custom_contract()
            fingerprint = compute_partition_fingerprint("custom", [command])
            catalog = catalog_store.build_catalog([command], fingerprint)
            text = catalog_store.render_catalog(catalog)

            first = catalog_store.save_catalog(
                catalog_path,
                text,
                expected_digest=None,
            )
            self.assertEqual(catalog_store.WRITE_WRITTEN, first)
            observed = catalog_store.read_catalog_state(catalog_path)

            with mock.patch.object(
                catalog_store,
                "atomic_write",
                side_effect=AssertionError("identical catalog was rewritten"),
            ):
                second = catalog_store.save_catalog(
                    catalog_path,
                    text,
                    expected_digest=observed.digest,
                    )
            self.assertEqual(catalog_store.WRITE_UNCHANGED, second)

            concurrent = b'{"changedBy":"another writer"}\n'
            catalog_path.write_bytes(concurrent)
            conflict = catalog_store.save_catalog(
                catalog_path,
                text,
                expected_digest=observed.digest,
            )
            self.assertEqual(catalog_store.WRITE_CONFLICT, conflict)
            self.assertEqual(concurrent, catalog_path.read_bytes())

            duplicate = text.replace(
                '"catalogVersion": 2,',
                '"catalogVersion": 2,\n  "catalogVersion": 2,',
                1,
            )
            catalog_path.write_text(duplicate, "utf-8")
            invalid = catalog_store.read_catalog_state(catalog_path)
            self.assertIsNone(invalid.catalog)
            self.assertIn("duplicate JSON field", invalid.invalid_reason)

    def test_status_does_not_probe_command_registry(self):
        class HealthOnlySession:
            def health(self):
                return {
                    "ok": True,
                    "data": {
                        "editorState": "Idle",
                        "unityVersion": "2022.3.10f1",
                    },
                }

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Packages").mkdir()
            args = SimpleNamespace(port=14500, mode="editor")
            stdout = io.StringIO()
            session = HealthOnlySession()

            with (
                mock.patch.object(core_bridge, "find_package_dir", return_value=root),
                mock.patch.object(CS, "_new_session", return_value=session),
                mock.patch.object(
                    registry_resolver,
                    "RegistryResolver",
                    side_effect=AssertionError(
                        "status must not consume the session registry comparison"
                    ),
                ),
                mock.patch.object(
                    paths,
                    "cache_root",
                    return_value=root / "cache",
                ),
                contextlib.redirect_stdout(stdout),
            ):
                status = CS._cmd_status_json(root, args, root)

            self.assertEqual(0, status)
            result = json.loads(stdout.getvalue())
            self.assertEqual(
                "Connected to Unity 2022 (Idle)",
                result["summary"],
            )
            self.assertNotIn("2022.3", result["summary"])
            self.assertNotIn("commands", result["data"])


if __name__ == "__main__":
    unittest.main()
