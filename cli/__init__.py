"""Unity C# Console CLI package."""

import json
from pathlib import Path

PACKAGE_NAME = "com.zh1zh1.csharpconsole"
DEFAULT_SOURCE = "https://github.com/niqibiao/unity-csharpconsole.git"
DEFAULT_EDITOR_PORT = 14500
DEFAULT_RUNTIME_PORT = 15500

# Cache file lives in the plugin directory, keyed by agent working directory.
_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_CACHE_FILE = _PLUGIN_DIR / ".pkg-cache.json"


def _save_json(path, data):
    """Write *data* as pretty JSON to *path*; silently no-op if the directory is
    read-only (e.g. an agent's plugin cache) — these files are only caches."""
    try:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8"
        )
    except OSError:
        pass


def _load_cache():
    try:
        return json.loads(_CACHE_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(data):
    _save_json(_CACHE_FILE, data)


def _agent_key(agent_root):
    """Normalize agent working directory to a stable cache key."""
    return str(Path(agent_root).resolve())


def save_pkg_path(agent_root, pkg_dir):
    """Cache the resolved package directory, keyed by agent working directory."""
    key = _agent_key(agent_root)
    data = _load_cache()
    data[key] = str(Path(pkg_dir).resolve())
    _save_cache(data)


def load_pkg_path(agent_root):
    """Load cached package directory for an agent root, or None if missing/invalid."""
    key = _agent_key(agent_root)
    data = _load_cache()
    path = data.get(key)
    if path:
        p = Path(path)
        if p.is_dir():
            return p
    return None


_CATALOG_CACHE_FILE = _PLUGIN_DIR / ".catalog-cache.json"


def _load_catalog_cache():
    try:
        return json.loads(_CATALOG_CACHE_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_catalog_cache(data):
    _save_json(_CATALOG_CACHE_FILE, data)


def save_catalog_path(project_root, catalog_file):
    """Cache the user-chosen catalog file path for a Unity project."""
    key = str(Path(project_root).resolve())
    data = _load_catalog_cache()
    data[key] = str(Path(catalog_file).resolve())
    _save_catalog_cache(data)


def load_catalog_path(project_root):
    """Load cached catalog file path for a Unity project, or None if not set."""
    key = str(Path(project_root).resolve())
    data = _load_catalog_cache()
    path = data.get(key)
    return Path(path) if path else None


def default_catalog_path(project_root):
    """Default catalog location: inside the Unity project under .unity-cli/."""
    return Path(project_root).resolve() / ".unity-cli" / "catalog.json"
