"""Audit and stats persistence for snippets."""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR_NAME = ".unity-cli"
AUDIT_FILE = "snippets-audit.json"
STATS_FILE = "snippets-stats.json"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def audit_path(project_root):
    return Path(project_root) / DATA_DIR_NAME / AUDIT_FILE


def stats_path(project_root):
    return Path(project_root) / DATA_DIR_NAME / STATS_FILE


def _load_json(path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")


def load_audit(project_root):
    return _load_json(audit_path(project_root), {"version": 1, "snippets": {}})


def save_audit(project_root, data):
    _save_json(audit_path(project_root), data)


def init_audit_entry(project_root, snippet_id, *, verified, when=None):
    when = when or _now()
    audit = load_audit(project_root)
    audit["snippets"][snippet_id] = {
        "created_at": when,
        "verified_at": when if verified else None,
        "unverified": not verified,
        "deprecated": False,
        "deprecated_at": None,
        "deprecated_reason": None,
        "supersedes": None,
    }
    save_audit(project_root, audit)


def mark_deprecated(project_root, snippet_id, *, reason=None, supersede=None, when=None):
    when = when or _now()
    audit = load_audit(project_root)
    if snippet_id not in audit["snippets"]:
        raise KeyError(snippet_id)
    e = audit["snippets"][snippet_id]
    e["deprecated"] = True
    e["deprecated_at"] = when
    e["deprecated_reason"] = reason
    e["supersedes"] = supersede
    save_audit(project_root, audit)


def load_stats(project_root):
    return _load_json(stats_path(project_root), {"version": 1, "snippets": {}})


def save_stats(project_root, data):
    _save_json(stats_path(project_root), data)


def init_stats_entry(project_root, snippet_id, *, created_at):
    stats = load_stats(project_root)
    stats["snippets"][snippet_id] = {
        "successes": 0,
        "failures": 0,
        "last_used": created_at,
        "last_failure": None,
        "first_failure_in_streak": None,
        "consecutive_failures": 0,
    }
    save_stats(project_root, stats)


def _ensure_entry(stats, snippet_id, when):
    return stats["snippets"].setdefault(snippet_id, {
        "successes": 0, "failures": 0, "last_used": when,
        "last_failure": None, "first_failure_in_streak": None,
        "consecutive_failures": 0,
    })


def record_success(project_root, snippet_id, *, when=None):
    when = when or _now()
    stats = load_stats(project_root)
    e = _ensure_entry(stats, snippet_id, when)
    e["successes"] += 1
    e["last_used"] = when
    e["consecutive_failures"] = 0
    e["first_failure_in_streak"] = None
    save_stats(project_root, stats)


def record_failure(project_root, snippet_id, *, when=None):
    when = when or _now()
    stats = load_stats(project_root)
    e = _ensure_entry(stats, snippet_id, when)
    e["failures"] += 1
    e["last_used"] = when
    e["last_failure"] = when
    if e["consecutive_failures"] == 0:
        e["first_failure_in_streak"] = when
    e["consecutive_failures"] += 1
    save_stats(project_root, stats)
