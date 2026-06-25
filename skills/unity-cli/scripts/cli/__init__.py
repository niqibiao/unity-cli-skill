"""Unity C# Console CLI package."""

from pathlib import Path

PACKAGE_NAME = "com.zh1zh1.csharpconsole"
DEFAULT_SOURCE = "https://github.com/niqibiao/unity-csharpconsole.git"
DEFAULT_EDITOR_PORT = 14500
DEFAULT_RUNTIME_PORT = 15500


def save_pkg_path(project_root, pkg_dir):
    """Cache the resolved Unity package dir for a project — machine-local, in the
    user's home cache, keyed by the resolved project root (so multiple projects on
    one machine never share a file). Silent if the cache dir is unwritable."""
    from cli.paths import state_dir, atomic_write
    atomic_write(state_dir(project_root) / "pkg-dir", str(Path(pkg_dir).resolve()))


def load_pkg_path(project_root):
    """Load the cached package dir for a project, or None if missing/stale."""
    from cli.paths import state_dir
    try:
        p = Path((state_dir(project_root) / "pkg-dir").read_text("utf-8").strip())
    except OSError:
        return None
    return p if p.is_dir() else None


def default_catalog_path(project_root):
    """Default catalog location: inside the Unity project under .unity-cli/ (committed)."""
    return Path(project_root).resolve() / ".unity-cli" / "catalog.json"
