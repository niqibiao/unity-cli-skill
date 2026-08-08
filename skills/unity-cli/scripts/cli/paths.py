"""Machine-local state location for unity-cli.

Resolved package paths, command registries, and snippet usage stats live in a
per-project subdir under the user's home cache. None of this state is written
into the project tree or committed skill directory.
"""

import hashlib
import os
import re
import tempfile
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


def registry_cache_path(project_root):
    """Return the per-project command-registry cache path."""
    return state_dir(project_root) / "command-registry.v1.json"


def atomic_write(path, text):
    """Atomically replace *path* with UTF-8/LF *text*.

    A unique temporary file in the destination directory prevents writers from
    sharing scratch state. Failures leave the destination untouched and return
    ``False``.
    """
    path = Path(path)
    temp_path = None
    descriptor = None
    try:
        if not isinstance(text, str):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        temp_path = Path(temp_name)
        stream = os.fdopen(
            descriptor,
            mode="w",
            encoding="utf-8",
            newline="\n",
        )
        descriptor = None
        with stream:
            stream.write(text.replace("\r\n", "\n").replace("\r", "\n"))
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temp_path, path)
        temp_path = None
        return True
    except (OSError, UnicodeError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass
