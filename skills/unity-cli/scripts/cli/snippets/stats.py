"""Audit and stats persistence for snippets."""

import json
from datetime import datetime, timezone
from pathlib import Path

from cli.snippets import DATA_DIR_NAME

AUDIT_FILE = "snippets-audit.json"
STATS_FILE = "snippets-stats.json"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def audit_path(project_root):
    return Path(project_root) / DATA_DIR_NAME / AUDIT_FILE


def stats_path(project_root):
    """Usage stats are machine-local observability — stored in the home cache, not
    the project tree (nothing to gitignore; multi-project runs don't collide). The
    audit file (above) stays in the project as committed history."""
    from cli.paths import state_dir
    return state_dir(project_root) / STATS_FILE


class SnippetDataError(Exception):
    """A committed snippet data file exists but is unreadable/corrupt.

    Raised (in strict mode) instead of silently returning an empty default,
    so write commands don't overwrite corrupted project state.
    """


def _load_json(path, default, strict=False):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        if strict:
            raise SnippetDataError(
                f"{path.name} exists but could not be read/parsed ({e}); "
                f"refusing to proceed so committed audit history is not "
                f"overwritten — fix or remove the file"
            )
        return default


def _save_json(path, data):
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    os.replace(tmp, path)  # atomic; raises on error to protect committed audit history


def load_audit(project_root):
    # Audit is committed project state — corruption must surface, not silently
    # reset to empty (which a subsequent save would commit, dropping history).
    return _load_json(audit_path(project_root), {"version": 1, "snippets": {}},
                      strict=True)


def save_audit(project_root, data):
    _save_json(audit_path(project_root), data)


def init_audit_entry(project_root, snippet_id, *, verified, when=None):
    when = when or _now()
    audit = load_audit(project_root)
    if snippet_id in audit["snippets"]:
        raise KeyError(f"audit entry already exists: {snippet_id!r}")
    audit["snippets"][snippet_id] = {
        "created_at": when,
        "verified_at": when if verified else None,
        "unverified": not verified,
        "deprecated": False,
        "deprecated_at": None,
        "deprecated_reason": None,
        "superseded_by": None,
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
    e["superseded_by"] = supersede
    save_audit(project_root, audit)


def load_stats(project_root):
    return _load_json(stats_path(project_root), {"version": 1, "snippets": {}})


def save_stats(project_root, data):
    _save_json(stats_path(project_root), data)


def init_stats_entry(project_root, snippet_id, *, created_at):
    stats = load_stats(project_root)
    if snippet_id in stats["snippets"]:
        raise KeyError(f"stats entry already exists: {snippet_id!r}")
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


# ---------------------------------------------------------------------------
# Aging policy classification
# ---------------------------------------------------------------------------

from collections import namedtuple

AgingPolicy = namedtuple("AgingPolicy", [
    "cold_days", "cold_min_uses",
    "broken_strikes", "broken_min_span_days",
    "hot_min_uses", "hot_max_recency_days",
])

DEFAULT_POLICY = AgingPolicy(
    cold_days=90, cold_min_uses=3,
    broken_strikes=5, broken_min_span_days=7,
    hot_min_uses=10, hot_max_recency_days=7,
)


def _parse_iso(s):
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _days_between(a, b):
    da, db = _parse_iso(a), _parse_iso(b)
    if da is None or db is None:
        return 0
    return abs((db - da).days)


def auto_deprecate_if_broken(project_root, snippet_id, policy=DEFAULT_POLICY):
    """Auto-deprecate *snippet_id* if its failure streak qualifies as broken.

    Called by `use` right after a Run-body failure is recorded (the spec's
    sanctioned trigger point — default `prune` never touches live snippets).
    Returns True if the snippet was deprecated by this call.
    """
    audit = load_audit(project_root)
    entry = audit["snippets"].get(snippet_id)
    if entry is None or entry.get("deprecated"):
        return False
    stats_entry = load_stats(project_root)["snippets"].get(snippet_id, {})
    if classify_state(stats_entry, _now(), policy) != "broken":
        return False
    span = _days_between(
        stats_entry.get("first_failure_in_streak"),
        stats_entry.get("last_failure"),
    )
    reason = (f"{stats_entry.get('consecutive_failures')} consecutive failures "
              f"over {span}d since {stats_entry.get('first_failure_in_streak')}")
    mark_deprecated(project_root, snippet_id, reason=reason)
    return True


def classify_state(entry, now_iso, policy=DEFAULT_POLICY):
    """Classify a stats entry as 'hot' / 'cold' / 'broken' / 'neutral'."""
    cs = entry.get("consecutive_failures", 0)
    if cs >= policy.broken_strikes:
        span = _days_between(
            entry.get("first_failure_in_streak"),
            entry.get("last_failure"),
        )
        if span >= policy.broken_min_span_days:
            return "broken"

    last_used = entry.get("last_used")
    successes = entry.get("successes", 0)
    if last_used:
        recency = _days_between(last_used, now_iso)
        if successes >= policy.hot_min_uses and recency <= policy.hot_max_recency_days:
            return "hot"
        if recency >= policy.cold_days and successes < policy.cold_min_uses:
            return "cold"
    return "neutral"
