"""Version checks for unity-cli — CLI ↔ Unity-package compatibility (check only).

No version *management* (no pinning, tag-matching, or remote update checks); the
CLI version is fixed by the committed VERSION file, and the only runtime concern is
warning when the installed Unity package is on a different major.minor line.
"""

import json
import re
from pathlib import Path

_SEMVER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_semver(version_str):
    """Extract (major, minor, patch) from a version string, or None."""
    if not version_str:
        return None
    m = _SEMVER_RE.search(str(version_str))
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def is_aligned(v1, v2):
    """True if two version strings share the same major.minor."""
    a, b = parse_semver(v1), parse_semver(v2)
    if a is None or b is None:
        return True  # can't compare → assume aligned
    return a[0] == b[0] and a[1] == b[1]


def get_plugin_version(cli_dir=None):
    """Read the CLI version from the bundled VERSION file (inside cli/, next to
    cs.py). Returns the version string, or 'unknown' if it can't be read."""
    try:
        base = Path(cli_dir) if cli_dir is not None else Path(__file__).resolve().parent
        return (base / "VERSION").read_text("utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def get_package_version(pkg_dir):
    """Read package version from <pkg_dir>/package.json. Returns str or None."""
    try:
        pj = Path(pkg_dir) / "package.json"
        data = json.loads(pj.read_text("utf-8"))
        return data.get("version")
    except Exception:
        return None
