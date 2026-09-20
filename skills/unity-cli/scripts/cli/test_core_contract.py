"""Bind the bridge's calls against a real csharpconsole_core, not a stand-in.

The rest of the suite hands ``ConsoleSession`` hand-written stubs, so it cannot
see the package's API move underneath it -- a renamed constant or a dropped
argument keeps every stub-backed test green and fails only against Unity. This
test closes that seam by resolving a real core and checking that every call the
bridge makes still binds.

The core is deliberately not bundled (see AGENTS.md, "Architecture"): it is
resolved from the installed Unity package so client and service stay aligned.
So this runs only where a real one is reachable -- set ``UNITY_CLI_CORE_DIR`` to
a package's ``Editor/ExternalTool~/console-client`` directory, or run from a
checkout that sits inside a Unity project with the package installed. It skips
everywhere else rather than pretending to have checked.
"""

import ast
import os
import sys
import unittest
from inspect import signature
from pathlib import Path

CLI_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = CLI_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

BRIDGE = CLI_DIR / "core_bridge.py"


def _core_dir():
    """A directory holding a real ``csharpconsole_core``, or None."""
    env = os.environ.get("UNITY_CLI_CORE_DIR")
    if env and (Path(env) / "csharpconsole_core").is_dir():
        return Path(env)
    from cli.core_bridge import CORE_RELATIVE, find_package_dir
    for parent in CLI_DIR.parents:
        if (parent / "Assets").is_dir() and (parent / "ProjectSettings").is_dir():
            pkg_dir = find_package_dir(parent)
            if pkg_dir and (pkg_dir / CORE_RELATIVE / "csharpconsole_core").is_dir():
                return pkg_dir / CORE_RELATIVE
    return None


CORE_DIR = _core_dir()


def _aliases(tree):
    """Map ``self._x`` and bare module names to the core module each names.

    Read off the source rather than restated here, so renaming an attribute in
    ``ConsoleSession.__init__`` cannot leave this test checking the wrong thing.
    """
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("csharpconsole_core"):
            for alias in node.names:
                modules.add(alias.asname or alias.name)
    alias_of = {name: name for name in modules}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if (isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name) and target.value.id == "self"
                and isinstance(value, ast.Name) and value.id in modules):
            alias_of[f"self.{target.attr}"] = value.id
    return alias_of


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return f"{head}.{node.attr}" if head else None
    return None


@unittest.skipUnless(CORE_DIR, "no real csharpconsole_core reachable (set UNITY_CLI_CORE_DIR)")
class CoreContractTests(unittest.TestCase):
    """Every core symbol the bridge reaches for must exist and accept its call."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(CORE_DIR))
        cls.tree = ast.parse(BRIDGE.read_text("utf-8"))
        cls.alias_of = _aliases(cls.tree)

    def _module(self, name):
        import importlib
        return importlib.import_module(f"csharpconsole_core.{name}")

    def test_every_attribute_the_bridge_reaches_for_exists(self):
        missing = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Attribute):
                continue
            owner = _dotted(node.value)
            if owner not in self.alias_of:
                continue
            module = self._module(self.alias_of[owner])
            if not hasattr(module, node.attr):
                missing.append(f"core_bridge.py:{node.lineno}: {owner}.{node.attr}")
        self.assertEqual([], missing)

    def test_every_call_binds_against_the_real_signature(self):
        wrong = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = _dotted(node.func.value)
            if owner not in self.alias_of:
                continue
            target = getattr(self._module(self.alias_of[owner]), node.func.attr, None)
            if not callable(target):
                continue
            keywords = [kw.arg for kw in node.keywords if kw.arg]
            if any(kw.arg is None for kw in node.keywords) or any(
                isinstance(a, ast.Starred) for a in node.args
            ):
                continue  # unpacked call sites cannot be bound statically
            try:
                signature(target).bind(
                    *([None] * len(node.args)), **{k: None for k in keywords}
                )
            except TypeError as error:
                wrong.append(
                    f"core_bridge.py:{node.lineno}: {owner}.{node.func.attr} -- {error}"
                )
        self.assertEqual([], wrong)
