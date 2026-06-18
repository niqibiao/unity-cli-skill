"""Version alignment checks for unity-cli-plugin."""

import base64
import json
import re
import subprocess
import urllib.request
import urllib.error
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
    """Read plugin version from .claude-plugin/plugin.json. Returns str or 'unknown'.

    cli_dir: a cli/ directory path; defaults to this file's own cli/ directory.
    Location-relative so it works for both the installed plugin and any copy."""
    try:
        base = Path(cli_dir) if cli_dir is not None else Path(__file__).resolve().parent
        pj = base.parent / ".claude-plugin" / "plugin.json"
        data = json.loads(pj.read_text("utf-8"))
        return data.get("version", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


def get_package_version(pkg_dir):
    """Read package version from <pkg_dir>/package.json. Returns str or None."""
    try:
        pj = Path(pkg_dir) / "package.json"
        data = json.loads(pj.read_text("utf-8"))
        return data.get("version")
    except Exception:
        return None


_GITHUB_RE = re.compile(
    r"(?:https?://github\.com/|git@github\.com:)([^/#]+)/([^/#]+?)(?:\.git)?(?:#.*)?$"
)


def _github_owner_repo(source):
    """Extract 'owner/repo' from a GitHub URL. Returns str or None."""
    m = _GITHUB_RE.search(str(source))
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def fetch_remote_version(source, timeout=5):
    """Fetch the version from the remote repo's default-branch package.json.

    Primary: GitHub raw URL. Fallback: GitHub contents API.
    Returns version string or None on failure.
    """
    owner_repo = _github_owner_repo(source)
    if not owner_repo:
        return None

    # Primary: raw.githubusercontent.com
    raw_url = f"https://raw.githubusercontent.com/{owner_repo}/main/package.json"
    try:
        req = urllib.request.Request(raw_url, headers={"User-Agent": "unity-cli-plugin"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("version")
    except Exception:
        pass

    # Fallback: GitHub API
    api_url = f"https://api.github.com/repos/{owner_repo}/contents/package.json"
    try:
        req = urllib.request.Request(api_url, headers={
            "User-Agent": "unity-cli-plugin",
            "Accept": "application/vnd.github.v3+json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
            content = base64.b64decode(meta["content"]).decode("utf-8")
            data = json.loads(content)
            return data.get("version")
    except Exception:
        return None


def check_versions(pkg_dir, source, timeout=5):
    """Run all version checks. Returns a dict with structured results.

    Keys: plugin, package, remote, aligned, updateAvailable
    """
    plugin_ver = get_plugin_version()
    package_ver = get_package_version(pkg_dir)
    remote_ver = fetch_remote_version(source, timeout=timeout)

    aligned = is_aligned(plugin_ver, package_ver) if package_ver else True
    update_available = False
    if remote_ver and package_ver:
        rv, pv = parse_semver(remote_ver), parse_semver(package_ver)
        if rv and pv:
            update_available = rv > pv

    return {
        "plugin": plugin_ver,
        "package": package_ver,
        "remote": remote_ver,
        "aligned": aligned,
        "updateAvailable": update_available,
    }


_TAG_LINE_RE = re.compile(r"^[0-9a-f]+\s+refs/tags/(?P<name>.+?)(?:\^\{\})?$")


def find_matching_tag(source, plugin_version, timeout=10):
    """Discover the highest-patch git tag matching v{major}.{minor}.* in the
    remote at *source*, where major/minor come from *plugin_version*.

    Returns the tag name as it appears in the remote (e.g., 'v1.4.3'), or
    None on no-match, ls-remote failure, missing git, timeout, or
    unparseable plugin version.
    """
    target = parse_semver(plugin_version)
    if target is None:
        return None
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", source],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None

    seen = {}
    for line in result.stdout.splitlines():
        m = _TAG_LINE_RE.match(line.strip())
        if not m:
            continue
        name = m.group("name")
        sv = parse_semver(name)
        if sv is None:
            continue
        if sv[0] != target[0] or sv[1] != target[1]:
            continue
        # Higher patch wins; ties resolved by first-seen.
        cur = seen.get((sv[0], sv[1]))
        if cur is None or sv[2] > cur[1]:
            seen[(sv[0], sv[1])] = (name, sv[2])

    match = seen.get((target[0], target[1]))
    return match[0] if match else None
