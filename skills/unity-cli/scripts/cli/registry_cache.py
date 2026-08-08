"""Validated per-project storage for package-owned command registries."""

import json
import os
from pathlib import Path

from cli.paths import atomic_write
from cli.registry_protocol import RegistryProtocolError, validate_snapshot


CACHE_VERSION = 1

_EXPECTED_GENERATION_UNSET = object()


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _try_acquire_write_lock(cache_path):
    """Acquire the cache's cross-process lock without waiting."""
    lock_path = Path(cache_path).with_name(Path(cache_path).name + ".lock")
    lock_file = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = lock_path.open("a+b")
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
                os.fsync(lock_file.fileno())
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        return lock_file
    except (ImportError, OSError):
        if lock_file is not None:
            try:
                lock_file.close()
            except OSError:
                pass
        return None


def _release_write_lock(lock_file):
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    finally:
        try:
            lock_file.close()
        except OSError:
            pass


def load_registry_cache(path):
    """Return a complete, valid cached snapshot, or ``None`` on any damage."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            return None
        if document.get("cacheVersion") != CACHE_VERSION:
            return None

        snapshot = document.get("snapshot")
        validate_snapshot(
            snapshot,
            required_included=("builtin", "custom"),
        )
        return snapshot
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        RegistryProtocolError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        return None


def save_registry_cache(
    path,
    snapshot,
    expected_generation=_EXPECTED_GENERATION_UNSET,
):
    """Validate and atomically persist a complete registry snapshot.

    When ``expected_generation`` is supplied, replacement is abandoned if the
    destination's valid cache generation has changed since the caller began its
    resolution. Passing ``None`` explicitly means that no valid cache existed at
    the start.
    """
    write_lock = _try_acquire_write_lock(path)
    if write_lock is None:
        return False
    try:
        try:
            payload = _canonical_json(snapshot)
            candidate = json.loads(payload)
            validate_snapshot(
                candidate,
                required_included=("builtin", "custom"),
            )
            document = {
                "cacheVersion": CACHE_VERSION,
                "snapshot": candidate,
            }
            text = _canonical_json(document) + "\n"
        except (
            json.JSONDecodeError,
            RegistryProtocolError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            return False

        if expected_generation is not _EXPECTED_GENERATION_UNSET:
            current = load_registry_cache(path)
            current_generation = (
                current.get("registryGeneration")
                if current is not None
                else None
            )
            if current_generation != expected_generation:
                return False

        return atomic_write(path, text)
    finally:
        _release_write_lock(write_lock)
