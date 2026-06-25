"""Machine-local state location for unity-cli.

All non-committed state (resolved package path, snippet usage stats) lives in a
per-project subdir under the user's home cache — never in the project tree or the
committed skill dir. Keying by the resolved project root means multiple Unity
projects on one machine each get their own files, so concurrent runs across
projects never share (and never race on) a file.
"""

import hashlib
import os
import re
from pathlib import Path


def cache_root():
    """Cross-platform per-user cache root for unity-cli."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "unity-cli"


def project_key(project_root):
    """Stable, collision-free, human-recognizable key for a project root."""
    resolved = str(Path(project_root).resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(resolved).name) or "project"
    return f"{name}-{digest}"


def state_dir(project_root):
    """Per-project machine-local state dir (created on demand)."""
    d = cache_root() / project_key(project_root)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def atomic_write(path, text):
    """Write text to *path* atomically (temp + os.replace). Silent on read-only."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, "utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
