"""Unity C# Console CLI — thin dispatcher over csharpconsole_core."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Ensure the cli package is importable when run as a standalone script
_CLI_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_CLI_DIR) not in sys.path:
    sys.path.insert(0, os.path.dirname(_CLI_DIR))

from cli import PACKAGE_NAME, DEFAULT_EDITOR_PORT, DEFAULT_RUNTIME_PORT
from cli.version_check import get_plugin_version, is_aligned


def _is_unity_root(d):
    return (d / "Assets").is_dir() and (d / "ProjectSettings").is_dir()


def _scan_children(p):
    """Return the first child directory that is a Unity project root, or None."""
    for d in sorted(p.iterdir()):
        if d.is_dir() and _is_unity_root(d):
            return d
    return None


def find_project_root(hint=None):
    """Locate a Unity project root.

    If *hint* is provided (``--project``), use it directly, scan children,
    or walk up to find the root.  Otherwise: current directory, then children.
    """
    if hint:
        p = Path(hint).resolve()
        if _is_unity_root(p):
            return p
        child = _scan_children(p) if p.is_dir() else None
        if child:
            return child
        # Walk up from hint to find project root
        for parent in p.parents:
            if _is_unity_root(parent):
                return parent
        return None

    cwd = Path.cwd().resolve()

    if _is_unity_root(cwd):
        return cwd

    # D4: the CLI is committed inside the project (npx skills add --copy), so walk
    # up from cs.py's own location — this anchors on the project that owns this
    # skill copy, surviving an agent that cd'd into the skill dir or a cwd outside
    # the project. Before _scan_children so a parent holding several Unity projects
    # can't mis-select the first child. A global (-g) install finds nothing here.
    for parent in Path(__file__).resolve().parents:
        if _is_unity_root(parent):
            return parent

    child = _scan_children(cwd)
    if child:
        return child

    # Walk up from cwd
    for parent in cwd.parents:
        if _is_unity_root(parent):
            return parent

    return None


def detect_port(project_root):
    """Read the effective port from Temp/CSharpConsole/refresh_state.json."""
    try:
        state_file = Path(project_root) / "Temp" / "CSharpConsole" / "refresh_state.json"
        data = json.loads(state_file.read_text("utf-8"))
        port = data.get("effectivePort")
        if port:
            return int(port)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


# ── Output helpers ─────────────────────────────────────────────────────

_SLIM_DROP = {"stage", "type", "exitCode", "sessionId", "runId", "mode", "durationMs"}
_HEALTH_DROP = {"ok", "initialized", "isEditor", "port", "refreshing", "editorState",
                "packageVersion", "protocolVersion", "unityVersion", "operation",
                "accepted", "sessionsCleared", "exitPlayModeRequested", "message"}


def _slim_result(result):
    """Strip diagnostic fields from a result dict for compact agent output."""
    out = {k: v for k, v in result.items() if k not in _SLIM_DROP}
    data = out.get("data")
    if isinstance(data, dict):
        # command/list-commands/exec: flatten resultJson, parsing if it's a
        # JSON string so agents see structured data instead of a stringified
        # blob. Falls back to the raw value on parse failure.
        if "resultJson" in data:
            rj = data["resultJson"]
            if isinstance(rj, str):
                try:
                    rj = json.loads(rj)
                except (ValueError, TypeError):
                    pass
            out["data"] = rj
        # command echo removal
        elif "command" in data and len(data) == 1:
            out.pop("data", None)
        # health/refresh: strip diagnostic fields
        elif "initialized" in data or "accepted" in data:
            trimmed = {k: v for k, v in data.items() if k not in _HEALTH_DROP}
            out["data"] = trimmed if trimmed else None
    # drop empty/redundant summary/data
    if out.get("summary") in ("", "OK"):
        out.pop("summary", None)
    if not out.get("data"):
        out.pop("data", None)
    return out


def _print_envelope(result, as_json):
    if as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        if result.get("ok"):
            print(result.get("summary", "OK"))
        else:
            print(f"Error: {result.get('summary', 'failed')}", file=sys.stderr)


# ── Pre-setup commands (pure stdlib, no core needed) ────────────────────

_PROGRESS_RE = re.compile(r"^(.+?):\s+(\d+)%\s+\((\d+)/(\d+)\)")


def _warn_version_mismatch(pkg_dir):
    """Print a warning if plugin and package versions are misaligned."""
    try:
        from cli.version_check import get_plugin_version, get_package_version, is_aligned, parse_semver
        plugin_ver = get_plugin_version()
        package_ver = get_package_version(pkg_dir)
        if package_ver and not is_aligned(plugin_ver, package_ver):
            pv_s = parse_semver(plugin_ver)
            kv_s = parse_semver(package_ver)
            pl = f"{pv_s[0]}.{pv_s[1]}.x" if pv_s else plugin_ver
            kl = f"{kv_s[0]}.{kv_s[1]}.x" if kv_s else package_ver
            print(f"\u26a0 version mismatch: plugin {pl} \u2260 package {kl}")
    except Exception:
        pass


def _new_session(root, args, pkg_dir):
    from cli.core_bridge import ConsoleSession
    return ConsoleSession(root, args.ip, args.port, args.mode, args.timeout,
                          pkg_dir=pkg_dir,
                          compile_ip=args.compile_ip, compile_port=args.compile_port)


def cmd_setup(root, args):
    """Locate the Unity project, cache the package path, and version-check.

    setup no longer installs the package: the user provides it (committed into the
    project, or added via UPM). setup just confirms where project + package are and
    warns on a CLI/package major.minor mismatch. Any other command lazily does the
    same locate+cache on first run, so setup is a convenience, not a gate.
    Returns 0 when a project is found, 1 otherwise."""
    if root is None:
        print("Error: no Unity project found (need Assets/ + ProjectSettings/).",
              file=sys.stderr)
        return 1
    print(f"Unity project : {root}")
    from cli.core_bridge import find_package_dir
    pkg_dir = find_package_dir(root)
    if pkg_dir:
        print(f"package       : {pkg_dir}")
        _warn_version_mismatch(pkg_dir)
        print("Ready. Run `cs status` to verify the live service.")
    else:
        print(f"package       : NOT FOUND ({PACKAGE_NAME})")
        print(f"Install {PACKAGE_NAME} into this project (UPM, or commit it), "
              f"then re-run `cs setup`.")
    return 0


def _cmd_status_json(root, args, agent_root=None):
    """JSON branch for cmd_status — emits a structured result envelope."""
    result = {"ok": False, "exitCode": 1, "summary": "", "data": {}}
    data = result["data"]

    # ── project ──────────────────────────────────────────────────────────
    if root is not None:
        data["project"] = {"path": str(root), "detected": True}
    else:
        data["project"] = {"path": None, "detected": False}

    # ── package ──────────────────────────────────────────────────────────
    pkg_dir = None
    try:
        from cli.core_bridge import find_package_dir
        pkg_dir = find_package_dir(root, agent_root) if root else None
        if pkg_dir:
            # Determine location type (manifest file: vs package cache)
            loc = "packageCache"
            manifest = root / "Packages" / "manifest.json"
            try:
                mdata = json.loads(manifest.read_text("utf-8"))
                entry = mdata.get("dependencies", {}).get(PACKAGE_NAME, "")
                if entry.startswith("file:"):
                    loc = "local"
                elif entry:
                    loc = "manifest"
            except Exception:
                pass
            from cli.version_check import get_package_version
            pkg_ver = get_package_version(pkg_dir)
            data["package"] = {
                "installed": True,
                "version": pkg_ver,
                "location": loc,
            }
        else:
            data["package"] = {"installed": False, "version": None, "location": None}
    except Exception as e:
        data["package"] = {"installed": False, "version": None, "location": None, "error": str(e)}

    # ── service / editor ─────────────────────────────────────────────────
    if pkg_dir:
        try:
            s = _new_session(root, args, pkg_dir)
            r = s.health()
            if r.get("ok"):
                hdata = r.get("data", {})
                data["service"] = {
                    "reachable": True,
                    "port": args.port,
                    "mode": args.mode,
                }
                data["editor"] = {
                    "state": hdata.get("editorState"),
                    "compiling": hdata.get("isCompiling", False),
                    "refreshing": hdata.get("refreshing", False),
                    "compileFailed": hdata.get("compileFailed", False),
                }
                # Build summary from live data
                unity_ver = hdata.get("unityVersion", "")
                editor_state = hdata.get("editorState", "")
                if unity_ver:
                    result["summary"] = f"Connected to Unity {unity_ver} ({editor_state})" if editor_state else f"Connected to Unity {unity_ver}"
                else:
                    result["summary"] = f"Connected ({editor_state})" if editor_state else "Connected"
                result["ok"] = True
                result["exitCode"] = 0
            else:
                data["service"] = {"reachable": False, "port": args.port, "mode": args.mode}
                data["editor"] = None
                result["summary"] = "Service unreachable"
        except Exception as e:
            data["service"] = {"reachable": False, "port": args.port, "mode": args.mode, "error": str(e)}
            data["editor"] = None
            result["summary"] = f"Service error: {e}"
    else:
        data["service"] = {"reachable": False, "port": args.port, "mode": args.mode}
        data["editor"] = None
        if root is None:
            result["summary"] = "No Unity project found"
        else:
            result["summary"] = "Package not installed"

    # ── versions ─────────────────────────────────────────────────────────
    try:
        from cli.version_check import get_plugin_version, get_package_version, is_aligned
        plugin_ver = get_plugin_version()
        pkg_ver = get_package_version(pkg_dir) if pkg_dir else None
        aligned = is_aligned(plugin_ver, pkg_ver) if pkg_ver else None
        data["versions"] = {
            "plugin": plugin_ver,
            "package": pkg_ver,
            "aligned": aligned,
        }
    except Exception as e:
        data["versions"] = {"plugin": None, "package": None, "aligned": None, "error": str(e)}

    # ── commands ─────────────────────────────────────────────────────────
    if pkg_dir and data.get("service", {}).get("reachable"):
        try:
            s2 = _new_session(root, args, pkg_dir)
            lc = s2.list_commands()
            if lc.get("ok"):
                lc_data = lc.get("data", {})
                rj = lc_data.get("resultJson", lc_data)
                if isinstance(rj, str):
                    try:
                        rj = json.loads(rj)
                    except (ValueError, TypeError):
                        rj = {}
                commands = rj.get("commands", [])
                builtin_count = sum(1 for c in commands if c.get("commandType", "builtin") == "builtin")
                custom_count = sum(1 for c in commands if c.get("commandType") == "custom")
                data["commands"] = {"builtin": builtin_count, "custom": custom_count}
            else:
                data["commands"] = {"builtin": None, "custom": None}
        except Exception as e:
            data["commands"] = {"builtin": None, "custom": None, "error": str(e)}
    else:
        data["commands"] = {"builtin": None, "custom": None}

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return result["exitCode"]


def cmd_status(root, args, agent_root=None):
    if args.as_json:
        return _cmd_status_json(root, args, agent_root)

    if root is None:
        print("unity_project: NOT FOUND")
        return 1
    print(f"unity_project: {root}")

    from cli.core_bridge import find_package_dir
    pkg_dir = find_package_dir(root, agent_root)
    if pkg_dir:
        print(f"package: {pkg_dir}")
    else:
        print("package: NOT FOUND")
    if not pkg_dir:
        return 0

    try:
        s = _new_session(root, args, pkg_dir)
        r = s.health()
        if r.get("ok"):
            data = r.get("data", {})
            print(f"service: OK (port {args.port}, {args.mode})")
            pkg_ver = data.get("packageVersion")
            proto_ver = data.get("protocolVersion")
            unity_ver = data.get("unityVersion")
            if pkg_ver:
                ver_parts = [pkg_ver]
                if proto_ver is not None:
                    ver_parts.append(f"protocol v{proto_ver}")
                if unity_ver:
                    ver_parts.append(f"Unity {unity_ver}")
                print(f"version: {', '.join(ver_parts)}")
        else:
            print("service: UNREACHABLE")
    except Exception as e:
        print(f"service: ERROR ({e})")

    # Version alignment hint (local only — no network, no latency)
    try:
        from cli.version_check import get_plugin_version, get_package_version, parse_semver, is_aligned
        plugin_ver = get_plugin_version()
        package_ver = get_package_version(pkg_dir)
        if package_ver and not is_aligned(plugin_ver, package_ver):
            pv_s = parse_semver(plugin_ver)
            kv_s = parse_semver(package_ver)
            pl = f"{pv_s[0]}.{pv_s[1]}.x" if pv_s else plugin_ver
            kl = f"{kv_s[0]}.{kv_s[1]}.x" if kv_s else package_ver
            print(f"\u26a0 plugin {pl} \u2260 package {kl} \u2014 align the package, then re-run `cs setup`")
    except Exception:
        pass  # version check is best-effort, never block status
    return 0


def _filter_commands_by_type(result, type_filter):
    """Filter list-commands result by commandType field.

    Handles three response shapes:
      - data.resultJson as a JSON string (canonical post-2024 wire format)
      - data.resultJson as a parsed dict (pre-parsed by transport)
      - data.commands as a flat list (already-flattened response)
    In all three cases we update *both* resultJson and data.commands so
    downstream callers (notably _slim_result) see consistent filtered data.
    """
    if type_filter == "all":
        return result
    data = result.get("data", {})
    raw_rj = data.get("resultJson")
    if isinstance(raw_rj, str):
        try:
            rj = json.loads(raw_rj)
        except (ValueError, TypeError):
            return result
    elif isinstance(raw_rj, dict):
        rj = raw_rj
    else:
        rj = data
    commands = rj.get("commands", [])
    filtered = [c for c in commands if c.get("commandType", "builtin") == type_filter]
    rj = dict(rj)
    rj["commands"] = filtered
    data = dict(data)
    data["commands"] = filtered
    if isinstance(raw_rj, str):
        data["resultJson"] = json.dumps(rj)
    elif isinstance(raw_rj, dict):
        data["resultJson"] = rj
    result = dict(result)
    result["data"] = data
    return result


# ── Catalog commands ───────────────────────────────────────────────────

def _resolve_catalog_path(root, args):
    """Catalog file location: --catalog-path for this call only, else the project
    default {project}/.unity-cli/catalog.json. No persistence — the default is
    stable (and committed), so there is nothing to remember between runs."""
    from cli import default_catalog_path
    explicit = getattr(args, "catalog_path", None)
    if explicit:
        return Path(explicit).expanduser().resolve()
    return default_catalog_path(root)


def cmd_catalog_sync(root, args, agent_root):
    from cli.core_bridge import find_package_dir
    from datetime import datetime, timezone

    pkg_dir = find_package_dir(root, agent_root)
    if pkg_dir is None:
        print("Error: C# Console package not found. Run 'cs setup' first.", file=sys.stderr)
        return 1

    s = _new_session(root, args, pkg_dir)
    r = s.list_commands()
    if not r.get("ok"):
        msg = r.get("summary", "unknown error")
        if args.as_json:
            json.dump({"ok": False, "exitCode": 1, "summary": msg}, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print(f"Error: list-commands failed: {msg}", file=sys.stderr)
        return 1

    # Parse commands from response. Fail closed on malformed payloads —
    # never overwrite the existing catalog with empty data on a parse error.
    data = r.get("data", {})
    rj = data.get("resultJson", data)
    if isinstance(rj, str):
        try:
            rj = json.loads(rj)
        except (ValueError, TypeError) as e:
            msg = f"list-commands returned malformed resultJson: {e}"
            if args.as_json:
                json.dump({"ok": False, "exitCode": 1, "summary": msg},
                          sys.stdout, ensure_ascii=False, indent=2)
                print()
            else:
                print(f"Error: {msg}. Existing catalog preserved.", file=sys.stderr)
            return 1
    if not isinstance(rj, dict) or not isinstance(rj.get("commands"), list):
        msg = "list-commands response missing 'commands' list"
        if args.as_json:
            json.dump({"ok": False, "exitCode": 1, "summary": msg},
                      sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print(f"Error: {msg}. Existing catalog preserved.", file=sys.stderr)
        return 1
    commands = rj["commands"]

    # Filter to custom commands only
    custom = [c for c in commands if c.get("commandType") == "custom"]

    # Resolve where this project's catalog lives (prompts on first sync)
    cat_file = _resolve_catalog_path(root, args)

    # Load existing catalog to compute diff
    old_ids = set()
    if cat_file.is_file():
        try:
            old_data = json.loads(cat_file.read_text("utf-8"))
            old_ids = {e["id"] for e in old_data.get("commands", [])}
        except (OSError, json.JSONDecodeError, KeyError):
            pass

    # Build catalog entries. The live wire format uses `commandNamespace` and
    # `arguments`; accept both names so older package versions still sync.
    entries = []
    for c in custom:
        ns = c.get("commandNamespace") or c.get("namespace") or ""
        action = c.get("action", "")
        entry = {
            "id": f"{ns}.{action}",
            "namespace": ns,
            "action": action,
            "summary": c.get("summary", ""),
            "editorOnly": c.get("editorOnly", False),
            "args": c.get("arguments") or c.get("args") or [],
        }
        entries.append(entry)

    new_ids = {e["id"] for e in entries}
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)

    catalog = {
        "version": 1,
        "project": str(Path(root).resolve()),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "commands": entries,
    }

    cat_file.parent.mkdir(parents=True, exist_ok=True)
    cat_file.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", "utf-8")

    if args.as_json:
        result = {
            "ok": True,
            "exitCode": 0,
            "summary": f"Synced {len(entries)} custom command(s)",
            "data": {
                "catalogFile": str(cat_file),
                "total": len(entries),
                "added": added,
                "removed": removed,
            },
        }
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"Synced {len(entries)} custom command(s) to {cat_file}")
        if added:
            print(f"  added: {', '.join(added)}")
        if removed:
            print(f"  removed: {', '.join(removed)}")

    return 0


def cmd_catalog_list(root, args):
    from cli import default_catalog_path

    explicit = getattr(args, "catalog_path", None)
    if explicit:
        cat_file = Path(explicit).expanduser().resolve()
    else:
        cat_file = default_catalog_path(root)

    if not cat_file.is_file():
        msg = f"Catalog file does not exist: {cat_file}. Run 'cs catalog sync' first."
        if args.as_json:
            json.dump({"ok": False, "exitCode": 1, "summary": msg},
                      sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

    try:
        catalog = json.loads(cat_file.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error: failed to read catalog: {e}", file=sys.stderr)
        return 1

    if args.as_json:
        json.dump({"ok": True, "exitCode": 0, "data": catalog}, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        commands = catalog.get("commands", [])
        print(f"Catalog: {len(commands)} custom command(s)  (synced {catalog.get('discovered_at', '?')})")
        for c in commands:
            arg_names = [a.get("name", "?") for a in c.get("args", [])]
            args_str = f" [{', '.join(arg_names)}]" if arg_names else ""
            editor_tag = " [editor-only]" if c.get("editorOnly") else ""
            summary = c.get("summary", "")
            desc = f" - {summary}" if summary else ""
            print(f"  {c['id']}{args_str}{editor_tag}{desc}")

    return 0


def cmd_snippets_add(root, args, agent_root):
    from cli.snippets.store import (parse_snippet_file, write_snippet_file,
                                    SnippetParseError)
    from cli.snippets.validate import validate_snippet, ValidationError
    from cli.snippets.stats import (init_audit_entry, init_stats_entry,
                                    load_audit, save_audit, load_stats,
                                    save_stats, _now)
    from cli.core_bridge import find_package_dir

    try:
        text = Path(args.file).read_text(encoding="utf-8-sig")
    except OSError as e:
        _print_envelope(
            {"ok": False, "exitCode": 1, "summary": f"cannot read --file: {e}"},
            args.as_json,
        )
        return 1

    try:
        snip = parse_snippet_file(text)
    except SnippetParseError as e:
        _print_envelope(
            {"ok": False, "exitCode": 1, "summary": f"parse error: {e}"},
            args.as_json,
        )
        return 1

    if snip["id"] != args.snippet_id:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"id mismatch: file declares {snip['id']!r}, "
                        f"CLI got {args.snippet_id!r}"},
            args.as_json,
        )
        return 1

    audit = load_audit(root)
    if args.snippet_id in audit["snippets"]:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"snippet {args.snippet_id!r} already exists; "
                        f"use `cs snippets update`"},
            args.as_json,
        )
        return 1

    pkg_dir = find_package_dir(root, agent_root) if not args.no_validate else None
    if pkg_dir is None and not args.no_validate:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": "package not found and --no-validate not set"},
            args.as_json,
        )
        return 1

    code_runner = None
    if pkg_dir is not None:
        session = _new_session(root, args, pkg_dir)
        code_runner = session.exec

    try:
        # When no_validate=True, validate_snippet short-circuits before calling
        # code_runner, so passing None is safe. The earlier guard ensures
        # code_runner is not None whenever no_validate=False.
        validate_snippet(snip, code_runner, no_validate=args.no_validate)
    except ValidationError as e:
        _print_envelope(
            {"ok": False, "exitCode": 1, "summary": f"validation failed: {e}"},
            args.as_json,
        )
        return 1

    when = _now()
    try:
        init_audit_entry(root, args.snippet_id, verified=not args.no_validate, when=when)
        init_stats_entry(root, args.snippet_id, created_at=when)
        write_snippet_file(root, args.snippet_id, text)
    except Exception as e:
        # Rollback partial state on failure: remove file if written, audit entry if added.
        try:
            from cli.snippets.store import snippet_path
            p = snippet_path(root, args.snippet_id)
            if p.is_file():
                p.unlink()
        except Exception:
            pass
        try:
            audit = load_audit(root)
            audit["snippets"].pop(args.snippet_id, None)
            save_audit(root, audit)
            stats = load_stats(root)
            stats["snippets"].pop(args.snippet_id, None)
            save_stats(root, stats)
        except Exception:
            pass
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"failed to register {args.snippet_id}: {e}"},
            args.as_json,
        )
        return 1

    _print_envelope(
        {"ok": True, "exitCode": 0,
         "summary": f"registered {args.snippet_id}"
                    + (" (unverified)" if args.no_validate else "")},
        args.as_json,
    )
    return 0


def cmd_snippets_use(root, args, agent_root):
    from cli.snippets.store import read_snippet_file, parse_snippet_file
    from cli.snippets.render import render_submission
    from cli.snippets.stats import load_audit, record_success, record_failure
    from cli.core_bridge import find_package_dir

    text = read_snippet_file(root, args.snippet_id)
    if text is None:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"snippet not found: {args.snippet_id}"},
            args.as_json,
        )
        return 1

    from cli.snippets.store import SnippetParseError
    try:
        snip = parse_snippet_file(text)
    except SnippetParseError as e:
        _print_envelope(
            {"ok": False, "exitCode": 1, "summary": f"snippet file is corrupt: {e}"},
            args.as_json,
        )
        return 1

    if snip["id"] != args.snippet_id:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"id mismatch: file declares {snip['id']!r}, "
                        f"CLI got {args.snippet_id!r}"},
            args.as_json,
        )
        return 1

    audit = load_audit(root)
    audit_entry = audit["snippets"].get(args.snippet_id)
    if audit_entry and audit_entry.get("deprecated"):
        reason = audit_entry.get("deprecated_reason")
        suffix = f" ({reason})" if reason else ""
        print(f"warning: snippet {args.snippet_id!r} is deprecated{suffix}",
              file=sys.stderr)

    arg_values = {}
    if args.snippet_args:
        try:
            arg_values = json.loads(args.snippet_args)
        except json.JSONDecodeError as e:
            _print_envelope(
                {"ok": False, "exitCode": 1,
                 "summary": f"--args is not valid JSON: {e}"},
                args.as_json,
            )
            return 1
        if not isinstance(arg_values, dict):
            _print_envelope(
                {"ok": False, "exitCode": 1,
                 "summary": "--args must decode to a JSON object"},
                args.as_json,
            )
            return 1

    try:
        submission = render_submission(
            snippet_id=snip["id"],
            body=snip["body"],
            args_schema=snip["args"],
            arg_values=arg_values,
        )
    except ValueError as e:
        _print_envelope(
            {"ok": False, "exitCode": 1, "summary": f"arg error: {e}"},
            args.as_json,
        )
        return 1

    if args.dry_run:
        if args.as_json:
            _print_envelope(
                {"ok": True, "exitCode": 0, "summary": "dry run",
                 "data": {"submission": submission}},
                True,
            )
        else:
            print(submission)
        return 0

    pkg_dir = find_package_dir(root, agent_root)
    if pkg_dir is None:
        _print_envelope(
            {"ok": False, "exitCode": 1, "summary": "package not found"},
            args.as_json,
        )
        return 1
    session = _new_session(root, args, pkg_dir)
    code_runner = session.exec
    response = code_runner(submission)

    # Stats taxonomy: only Run-body errors (compile / runtime) count toward
    # the failure streak. Environment errors — Unity not running, network —
    # come back as ok=false envelopes with type "system_error" (core_bridge
    # wraps transport exceptions into envelopes; verified empirically) and
    # must not poison the streak, or offline retries would auto-deprecate
    # perfectly good snippets.
    recorded = False
    if response.get("ok") and response.get("exitCode", 0) == 0:
        record_success(root, args.snippet_id)
        recorded = True
    elif response.get("type") != "system_error":
        record_failure(root, args.snippet_id)
        recorded = True
        from cli.snippets.stats import auto_deprecate_if_broken
        if auto_deprecate_if_broken(root, args.snippet_id):
            print(f"warning: snippet {args.snippet_id!r} auto-deprecated "
                  f"after a qualifying failure streak "
                  f"(see `cs snippets stats --id {args.snippet_id}`)",
                  file=sys.stderr)

    if args.as_json:
        json.dump(response, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        session.emit(response)
    return response.get("exitCode", 0)


def cmd_snippets_list(root, args):
    from cli.snippets.store import (list_snippet_ids, read_snippet_file,
                                    parse_snippet_file)
    from cli.snippets.stats import load_audit, load_stats

    ids = list_snippet_ids(root)
    audit = load_audit(root)
    stats = load_stats(root)
    rows = []
    for sid in ids:
        a = audit["snippets"].get(sid, {})
        if a.get("deprecated") and not args.include_deprecated:
            continue
        text = read_snippet_file(root, sid) or ""
        try:
            snip = parse_snippet_file(text)
        except Exception:
            continue
        if args.safety and snip["safety"] != args.safety:
            continue
        st = stats["snippets"].get(sid, {})
        rows.append({
            "id": sid,
            "summary": snip["summary"],
            "safety": snip["safety"],
            "deprecated": a.get("deprecated", False),
            "unverified": a.get("unverified", False),
            "successes": st.get("successes", 0),
            "failures": st.get("failures", 0),
            "last_used": st.get("last_used"),
        })

    if args.sort == "hot":
        rows.sort(key=lambda r: -r["successes"])
    elif args.sort == "recent":
        rows.sort(key=lambda r: r["last_used"] or "", reverse=True)
    elif args.sort == "cold":
        rows.sort(key=lambda r: (r["successes"], r["last_used"] or ""))

    result = {
        "ok": True, "exitCode": 0,
        "summary": f"{len(rows)} snippet(s)",
        "data": {"snippets": rows},
    }
    if args.as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(result["summary"])
        for r in rows:
            tags = []
            if r["unverified"]:
                tags.append("UNVERIFIED")
            if r["deprecated"]:
                tags.append("DEPRECATED")
            tag_s = f" [{', '.join(tags)}]" if tags else ""
            print(f"  {r['id']} ({r['safety']}){tag_s} — {r['summary']}")
    return 0


def cmd_snippets_search(root, args):
    from cli.snippets.store import (list_snippet_ids, read_snippet_file,
                                    parse_snippet_file)
    from cli.snippets.stats import load_audit

    all_ids = list_snippet_ids(root)
    if not all_ids:
        # Empty-library fast path: tell the agent explicitly so it can skip
        # further snippet lookups this session instead of paying the search
        # tax on every non-trivial exec task.
        result = {
            "ok": True, "exitCode": 0,
            "summary": "snippet library is empty — skip snippet lookup and "
                       "go ad-hoc (cs exec); consider distilling afterwards",
            "data": {"results": [], "libraryEmpty": True},
        }
        if args.as_json:
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print(result["summary"])
        return 0

    audit = load_audit(root)
    q = args.query.lower()
    q_terms = [t for t in q.split() if t]
    hits = []
    for sid in all_ids:
        a = audit["snippets"].get(sid, {})
        if a.get("deprecated"):
            continue
        text = read_snippet_file(root, sid) or ""
        try:
            snip = parse_snippet_file(text)
        except Exception:
            continue
        haystack = f"{sid} {snip['summary']}".lower()
        score = sum(1 for t in q_terms if t in haystack)
        if score > 0:
            hits.append((score, sid, snip))
    hits.sort(key=lambda x: (-x[0], x[1]))
    top = hits[: args.top]
    rows = []
    for score, sid, snip in top:
        args_summary = ", ".join(
            f"{a['name']}:{a['type']}" for a in snip["args"]
        )
        rows.append({
            "id": sid, "summary": snip["summary"],
            "args": args_summary, "score": score,
        })
    result = {
        "ok": True, "exitCode": 0,
        "summary": f"{len(rows)} hit(s) for {args.query!r}",
        "data": {"results": rows},
    }
    if args.as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(result["summary"])
        for r in rows:
            print(f"  {r['id']}({r['args']}) — {r['summary']}")
    return 0


def cmd_snippets_update(root, args, agent_root):
    from cli.snippets.store import (read_snippet_file, parse_snippet_file,
                                    write_snippet_file, SnippetParseError)
    from cli.snippets.validate import validate_snippet, ValidationError
    from cli.snippets.stats import load_audit, save_audit, _now
    from cli.core_bridge import find_package_dir

    if not args.file and not args.set_field:
        _print_envelope(
            {"ok": False, "exitCode": 1, "summary": "must pass --file or --set"},
            args.as_json,
        )
        return 1

    existing = read_snippet_file(root, args.snippet_id)
    if existing is None:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"snippet not found: {args.snippet_id}"},
            args.as_json,
        )
        return 1

    if args.file:
        try:
            new_text = Path(args.file).read_text(encoding="utf-8-sig")
        except OSError as e:
            _print_envelope(
                {"ok": False, "exitCode": 1, "summary": f"cannot read --file: {e}"},
                args.as_json,
            )
            return 1
        try:
            new_snip = parse_snippet_file(new_text)
        except SnippetParseError as e:
            _print_envelope(
                {"ok": False, "exitCode": 1, "summary": f"parse error: {e}"},
                args.as_json,
            )
            return 1
        if new_snip["id"] != args.snippet_id:
            _print_envelope(
                {"ok": False, "exitCode": 1,
                 "summary": "id mismatch between --file and CLI argument"},
                args.as_json,
            )
            return 1

        pkg_dir = find_package_dir(root, agent_root) if not args.no_validate else None
        if pkg_dir is None and not args.no_validate:
            _print_envelope(
                {"ok": False, "exitCode": 1,
                 "summary": "package not found and --no-validate not set"},
                args.as_json,
            )
            return 1
        if pkg_dir is not None:
            session = _new_session(root, args, pkg_dir)
            code_runner = session.exec
        else:
            code_runner = None
        try:
            validate_snippet(new_snip, code_runner, no_validate=args.no_validate)
        except ValidationError as e:
            _print_envelope(
                {"ok": False, "exitCode": 1, "summary": f"validation failed: {e}"},
                args.as_json,
            )
            return 1

        # Check the audit entry BEFORE writing the body. An integrity-drift
        # case (file present, audit entry gone) must not leave a half-updated
        # snippet — a rewritten body with stale verified_at/unverified. Refuse
        # up front (fail-closed) and point at doctor, which reports this as an
        # orphan_file finding.
        audit = load_audit(root)
        e = audit["snippets"].get(args.snippet_id)
        if e is None:
            _print_envelope(
                {"ok": False, "exitCode": 1,
                 "summary": f"audit entry missing for {args.snippet_id}; "
                            f"refusing to update (run `cs snippets doctor`)"},
                args.as_json,
            )
            return 1

        write_snippet_file(root, args.snippet_id, new_text)
        if not args.no_validate:
            e["verified_at"] = _now()
            e["unverified"] = False
        else:
            e["verified_at"] = None
            e["unverified"] = True
        save_audit(root, audit)

        _print_envelope(
            {"ok": True, "exitCode": 0,
             "summary": f"updated {args.snippet_id}"
                        + (" (unverified)" if args.no_validate else "")},
            args.as_json,
        )
        return 0

    snip = parse_snippet_file(existing)
    new_summary = snip["summary"]
    arg_desc_updates = {}
    for kv in args.set_field:
        if "=" not in kv:
            _print_envelope(
                {"ok": False, "exitCode": 1,
                 "summary": f"--set expects key=value, got {kv!r}"},
                args.as_json,
            )
            return 1
        k, _, v = kv.partition("=")
        k = k.strip()
        v = v.strip()
        if k == "summary":
            new_summary = v
        elif k.startswith("arg.") and k.endswith(".description"):
            argname = k[4:-len(".description")]
            if not any(a["name"] == argname for a in snip["args"]):
                _print_envelope(
                    {"ok": False, "exitCode": 1,
                     "summary": f"no such arg: {argname!r}"},
                    args.as_json,
                )
                return 1
            arg_desc_updates[argname] = v
        else:
            _print_envelope(
                {"ok": False, "exitCode": 1,
                 "summary": f"--set field {k!r} not allowed; only `summary` and "
                            f"`arg.<name>.description` can be updated without --file"},
                args.as_json,
            )
            return 1

    new_text = existing
    if new_summary != snip["summary"]:
        new_text = re.sub(
            r"(^summary:).+$",
            lambda m: m.group(1) + " " + new_summary,
            new_text, count=1, flags=re.MULTILINE,
        )
    for argname, desc in arg_desc_updates.items():
        # Step 1: remove any existing description: line for this arg.
        # Match "- name: argname\n" followed by 4-space-indent lines, scoped
        # so we only edit lines belonging to this specific arg's block.
        block_re = re.compile(
            rf"(?P<head>- name: {re.escape(argname)}\n(?P<body>(?:    [^\n]+\n)*))",
            re.MULTILINE,
        )
        m = block_re.search(new_text)
        if not m:
            # The arg exists in parsed schema but block_re missed it; skip silently.
            continue
        body_lines = [ln for ln in m.group("body").splitlines(keepends=True)
                      if not ln.startswith("    description:")]
        body_lines.append(f"    description: {desc}\n")
        replacement = m.group("head").split("\n", 1)[0] + "\n" + "".join(body_lines)
        new_text = new_text[:m.start()] + replacement + new_text[m.end():]

    write_snippet_file(root, args.snippet_id, new_text)
    _print_envelope(
        {"ok": True, "exitCode": 0,
         "summary": f"updated {args.snippet_id} metadata"},
        args.as_json,
    )
    return 0


def cmd_snippets_deprecate(root, args):
    from cli.snippets.store import read_snippet_file
    from cli.snippets.stats import mark_deprecated, load_audit

    if read_snippet_file(root, args.snippet_id) is None:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"snippet not found: {args.snippet_id}"},
            args.as_json,
        )
        return 1
    audit = load_audit(root)
    if args.snippet_id not in audit["snippets"]:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"no audit entry for {args.snippet_id}"},
            args.as_json,
        )
        return 1
    if args.supersede and read_snippet_file(root, args.supersede) is None:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"--supersede target not found: {args.supersede}"},
            args.as_json,
        )
        return 1
    mark_deprecated(root, args.snippet_id,
                    reason=args.reason, supersede=args.supersede)
    _print_envelope(
        {"ok": True, "exitCode": 0,
         "summary": f"deprecated {args.snippet_id}"
                    + (f" (superseded by {args.supersede})" if args.supersede else "")},
        args.as_json,
    )
    return 0


def cmd_snippets_prune(root, args):
    from cli.snippets.store import (snippet_path, list_snippet_ids)
    from cli.snippets.stats import (load_audit, save_audit, load_stats,
                                    save_stats, classify_state, mark_deprecated, _now)
    from datetime import datetime, timezone

    audit = load_audit(root)
    stats = load_stats(root)

    actions = {"deprecate": [], "remove": []}
    now_iso = _now()

    # Default prune only acts on already-deprecated entries (spec: without
    # --remove it is a no-op). Broken-streak auto-deprecation happens at
    # `use` time, never here. --cold opts in to deprecating cold snippets.
    if args.cold:
        for sid in list_snippet_ids(root):
            a = audit["snippets"].get(sid)
            if a is None or a.get("deprecated"):
                continue
            entry = stats["snippets"].get(sid, {})
            if classify_state(entry, now_iso) == "cold":
                actions["deprecate"].append((sid, "cold"))

    if args.remove:
        now = datetime.now(timezone.utc)
        for sid, a in audit["snippets"].items():
            if not a.get("deprecated"):
                continue
            dep_at = a.get("deprecated_at")
            if not dep_at:
                continue
            d = datetime.strptime(dep_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if (now - d).days >= args.max_age_days:
                actions["remove"].append(sid)

    if args.dry_run:
        result = {
            "ok": True, "exitCode": 0,
            "summary": (f"plan: deprecate {len(actions['deprecate'])}, "
                        f"remove {len(actions['remove'])}"),
            "data": {
                "deprecate": [sid for sid, _ in actions["deprecate"]],
                "remove": actions["remove"],
            },
        }
        if args.as_json:
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print(result["summary"])
            for sid, st in actions["deprecate"]:
                print(f"  deprecate ({st}): {sid}")
            for sid in actions["remove"]:
                print(f"  remove:    {sid}")
        return 0

    for sid, _state in actions["deprecate"]:
        mark_deprecated(root, sid, reason="cold (low usage)")

    if actions["remove"]:
        # Reload: mark_deprecated persisted entries after our initial load;
        # writing back the stale in-memory copy would erase them.
        audit = load_audit(root)
        stats = load_stats(root)
        for sid in actions["remove"]:
            p = snippet_path(root, sid)
            try:
                p.unlink()
            except OSError:
                pass
            audit["snippets"].pop(sid, None)
            stats["snippets"].pop(sid, None)
        save_audit(root, audit)
        save_stats(root, stats)

    summary = (f"deprecated {len(actions['deprecate'])}, "
               f"removed {len(actions['remove'])}")
    _print_envelope(
        {"ok": True, "exitCode": 0, "summary": summary,
         "data": {
             "deprecate": [sid for sid, _ in actions["deprecate"]],
             "remove": actions["remove"],
         }},
        args.as_json,
    )
    return 0


def cmd_snippets_stats(root, args):
    from cli.snippets.stats import load_stats, classify_state, _now

    stats = load_stats(root)
    items = stats.get("snippets", {})
    rows = []
    now_iso = _now()
    target = (items.items() if not args.snippet_id
              else [(args.snippet_id, items.get(args.snippet_id))])
    for sid, entry in target:
        if entry is None:
            continue
        rows.append({
            "id": sid,
            "successes": entry.get("successes", 0),
            "failures": entry.get("failures", 0),
            "invocations": entry.get("successes", 0) + entry.get("failures", 0),
            "last_used": entry.get("last_used"),
            "consecutive_failures": entry.get("consecutive_failures", 0),
            "state": classify_state(entry, now_iso),
        })

    rows.sort(key=lambda r: -r["successes"])
    result = {
        "ok": True, "exitCode": 0,
        "summary": f"stats for {len(rows)} snippet(s)",
        "data": {"stats": rows},
    }
    if args.as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(result["summary"])
        for r in rows:
            print(f"  {r['id']:40s} successes={r['successes']:4d} "
                  f"failures={r['failures']:4d} state={r['state']}")
    return 0


def cmd_snippets_doctor(root, args, agent_root):
    """Library health check: integrity + staleness diagnosis (anti-rot).

    Read-only by default. With --revalidate, re-runs the validation gate on
    every live read-only snippet against the running Unity (catching API
    drift) and refreshes verified_at on passes. Never touches usage stats —
    doctor runs are diagnostics, not invocations.
    """
    from cli.snippets.store import (list_snippet_ids, read_snippet_file,
                                    parse_snippet_file, SnippetParseError)
    from cli.snippets.stats import (load_audit, save_audit, load_stats,
                                    classify_state, _now)
    from datetime import datetime, timezone

    audit = load_audit(root)
    stats = load_stats(root)
    now_iso = _now()
    file_ids = list_snippet_ids(root)
    audit_ids = set(audit["snippets"])

    findings = []

    def finding(ftype, sid, detail, action):
        findings.append({"type": ftype, "id": sid,
                         "detail": detail, "action": action})

    # --- integrity -------------------------------------------------------
    parsed = {}
    for sid in file_ids:
        if sid not in audit_ids:
            finding("orphan_file", sid,
                    "snippet file has no audit entry",
                    "re-register: cs snippets add <id> --file "
                    ".unity-cli/snippets~/<id>.md (or delete the file)")
        text = read_snippet_file(root, sid) or ""
        try:
            snip = parse_snippet_file(text)
        except SnippetParseError as e:
            finding("corrupt", sid, str(e),
                    "fix via cs snippets update --file, or deprecate")
            continue
        if snip["id"] != sid:
            finding("id_mismatch", sid,
                    f"file declares id {snip['id']!r}",
                    "fix via cs snippets update --file")
            continue
        parsed[sid] = snip

    for sid in sorted(audit_ids):
        if sid not in file_ids:
            finding("missing_file", sid,
                    "audit entry exists but the snippet file is gone",
                    "restore the file from git history, or remove the "
                    "audit entry by hand (snippets-audit.json is plain "
                    "project state)")

    live = [sid for sid in file_ids
            if sid in audit_ids and not audit["snippets"][sid].get("deprecated")]

    # --- staleness -------------------------------------------------------
    for sid in live:
        state = classify_state(stats["snippets"].get(sid, {}), now_iso)
        if state == "broken":
            finding("broken", sid,
                    "qualifying failure streak (>=5 over >=7d)",
                    "diagnose; cs snippets update --file or deprecate")
        elif state == "cold":
            finding("cold", sid,
                    "not used in 90d with <3 successes",
                    "informational; cs snippets prune --cold to retire")
        if audit["snippets"][sid].get("unverified"):
            finding("unverified", sid,
                    "mutates snippet was registered without validation",
                    "manual review")

    now = datetime.now(timezone.utc)
    for sid, a in sorted(audit["snippets"].items()):
        if a.get("deprecated") and a.get("deprecated_at"):
            d = datetime.strptime(a["deprecated_at"],
                                  "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if (now - d).days >= 30:
                finding("removable", sid,
                        f"deprecated since {a['deprecated_at']}",
                        "cs snippets prune --remove (destructive — confirm)")

    # --- live revalidation (opt-in) ---------------------------------------
    revalidated = None
    if args.revalidate:
        from cli.core_bridge import find_package_dir
        from cli.snippets.validate import validate_snippet, ValidationError

        pkg_dir = find_package_dir(root, agent_root)
        if pkg_dir is None:
            _print_envelope(
                {"ok": False, "exitCode": 1, "summary": "package not found"},
                args.as_json,
            )
            return 1
        session = _new_session(root, args, pkg_dir)
        health = session.health()
        if not health.get("ok"):
            _print_envelope(
                {"ok": False, "exitCode": 1,
                 "summary": "Unity service not reachable — revalidation "
                            f"needs a running editor ({health.get('summary')})"},
                args.as_json,
            )
            return 1

        passed = []
        failed = 0
        for sid in live:
            snip = parsed.get(sid)
            if snip is None or snip["safety"] != "read-only":
                continue
            try:
                validate_snippet(snip, session.exec)
            except ValidationError as e:
                failed += 1
                finding("revalidation_failed", sid, str(e),
                        "API drift? cs snippets update --file with a fixed "
                        "body, or deprecate")
            else:
                passed.append(sid)
        if passed:
            audit = load_audit(root)
            for sid in passed:
                # A read-only snippet that just passed the gate is verified —
                # clear the unverified flag too (e.g. one added with
                # --no-validate), or list/doctor would keep flagging it.
                audit["snippets"][sid]["verified_at"] = now_iso
                audit["snippets"][sid]["unverified"] = False
            save_audit(root, audit)
        revalidated = {"passed": len(passed), "failed": failed}

    counts = {}
    for f in findings:
        counts[f["type"]] = counts.get(f["type"], 0) + 1
    data = {"findings": findings, "counts": counts,
            "files": len(file_ids), "live": len(live)}
    if revalidated is not None:
        data["revalidated"] = revalidated
    result = {
        "ok": True, "exitCode": 0,
        "summary": f"{len(findings)} finding(s) across {len(file_ids)} "
                   f"snippet file(s)",
        "data": data,
    }
    if args.as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(result["summary"])
        for f in findings:
            print(f"  [{f['type']}] {f['id']} — {f['detail']}")
            print(f"      -> {f['action']}")
        if revalidated is not None:
            print(f"  revalidated: {revalidated['passed']} passed, "
                  f"{revalidated['failed']} failed")
    return 0


def cmd_snippets_show(root, args):
    from cli.snippets.store import read_snippet_file, parse_snippet_file
    from cli.snippets.stats import load_audit, load_stats

    text = read_snippet_file(root, args.snippet_id)
    if text is None:
        _print_envelope(
            {"ok": False, "exitCode": 1,
             "summary": f"snippet not found: {args.snippet_id}"},
            args.as_json,
        )
        return 1
    try:
        snip = parse_snippet_file(text)
    except Exception as e:
        _print_envelope(
            {"ok": False, "exitCode": 1, "summary": f"parse error: {e}"},
            args.as_json,
        )
        return 1
    audit = load_audit(root)["snippets"].get(args.snippet_id, {})
    stats = load_stats(root)["snippets"].get(args.snippet_id, {})

    if args.as_json:
        json.dump({
            "ok": True, "exitCode": 0,
            "summary": snip["summary"],
            "data": {
                "id": snip["id"], "summary": snip["summary"],
                "safety": snip["safety"], "args": snip["args"],
                "example": snip["example"],
                "expected": snip.get("expected"),
                "body": snip["body"],
                "audit": audit, "stats": stats,
            },
        }, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"{snip['id']} ({snip['safety']}) — {snip['summary']}")
        print()
        print("Args:")
        for spec in snip["args"]:
            default = f" = {spec['default']!r}" if "default" in spec else ""
            print(f"  {spec['name']}: {spec['type']}{default}")
        print()
        print("Example:", snip["example"])
        if snip.get("expected") is not None:
            print("Expected:", snip["expected"])
        print()
        print("Body:")
        print(snip["body"])
    return 0


# ── Main ────────────────────────────────────────────────────────────────

def main():
    # Shared flags available on every subcommand.
    # Use SUPPRESS so subparser parses don't overwrite values supplied to the
    # top-level parser (argparse parents+subparsers footgun).  Defaults are
    # filled in after parse_args via _SHARED_DEFAULTS.
    SUPPRESS = argparse.SUPPRESS
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--project", default=SUPPRESS, help="Unity project root (auto-detected)")
    shared.add_argument("--ip", default=SUPPRESS)
    shared.add_argument("--port", type=int, default=SUPPRESS)
    shared.add_argument("--mode", choices=["editor", "runtime"], default=SUPPRESS)
    shared.add_argument("--compile-ip", dest="compile_ip", default=SUPPRESS,
                        help="Editor/compile server IP (runtime mode only, default: 127.0.0.1)")
    shared.add_argument("--compile-port", dest="compile_port", type=int, default=SUPPRESS,
                        help="Editor/compile server port (runtime mode only, default: auto-detect)")
    shared.add_argument("--timeout", type=int, default=SUPPRESS, help="HTTP timeout in seconds (default: 30)")
    shared.add_argument("--json", dest="as_json", action="store_true", default=SUPPRESS,
                        help="JSON output (compact by default, use --verbose for full)")
    shared.add_argument("--verbose", action="store_true", default=SUPPRESS,
                        help="Full JSON output with all diagnostic fields")

    p = argparse.ArgumentParser(prog="cs", description="Unity C# Console CLI", parents=[shared])
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    sub.add_parser("setup", parents=[shared],
                   help="Locate project, cache package path, version-check (does not install the package)")

    sub.add_parser("status", parents=[shared], help="Package + connection status")

    sp_exec = sub.add_parser("exec", parents=[shared], help="Execute C# code")
    sp_exec.add_argument("code", nargs="?", help="C# code to execute (inline; omit when using --file)")
    sp_exec.add_argument("--file", "-f", dest="file",
                         help="Read C# code from a file")

    sp_cmd = sub.add_parser("command", parents=[shared], help="Run framework command")
    sp_cmd.add_argument("namespace", help="Command namespace")
    sp_cmd.add_argument("action", help="Command action")
    sp_cmd.add_argument("args", nargs="?", default=None, help="Arguments (JSON)")

    sub.add_parser("health", parents=[shared], help="Service health check")

    sp_refresh = sub.add_parser("refresh", parents=[shared], help="Trigger asset refresh and script compilation")
    sp_refresh.add_argument("--wait", type=int, nargs="?", const=60, default=None, metavar="TIMEOUT",
                            help="Wait for refresh to complete (default timeout: 60s)")
    sp_refresh.add_argument("--exit-playmode", action="store_true",
                            help="Exit play mode before refreshing if needed")
    sp_refresh.add_argument("--files", nargs="+", default=None, metavar="PATH",
                            help="Explicit asset paths to import (e.g. Assets/Scripts/Foo.cs)")

    sp_lc = sub.add_parser("list-commands", parents=[shared], help="List available commands")
    sp_lc.add_argument("--type", choices=["builtin", "custom", "all"], default="all",
                        dest="cmd_type", help="Filter by command type (default: all)")

    sp_cmp = sub.add_parser("complete", parents=[shared], help="Get completions")
    sp_cmp.add_argument("code")
    sp_cmp.add_argument("cursor", type=int)

    sp_batch = sub.add_parser("batch", parents=[shared], help="Execute multiple commands in one request")
    sp_batch.add_argument("commands", help="JSON array of commands")
    sp_batch.add_argument("--stop-on-error", action="store_true",
                          help="Stop executing on first error")

    sp_cat = sub.add_parser("catalog", parents=[shared], help="Manage custom command catalog")
    cat_sub = sp_cat.add_subparsers(dest="catalog_cmd")
    sp_cat_sync = cat_sub.add_parser("sync", parents=[shared], help="Sync catalog from live editor")
    sp_cat_sync.add_argument("--catalog-path", dest="catalog_path", default=None,
                             help="Catalog file path (default: prompt or {project}/.unity-cli/catalog.json)")
    sp_cat_list = cat_sub.add_parser("list", parents=[shared], help="List cached catalog")
    sp_cat_list.add_argument("--catalog-path", dest="catalog_path", default=None,
                             help="Override the cached catalog file path for this read")

    sp_sn = sub.add_parser("snippets", parents=[shared], help="Reusable C# snippet library")
    sn_sub = sp_sn.add_subparsers(dest="snippets_cmd")

    sp_sn_add = sn_sub.add_parser("add", parents=[shared], help="Validate and register a snippet")
    sp_sn_add.add_argument("snippet_id", help="Snippet id (dotted, e.g. scene.find_in_layer)")
    sp_sn_add.add_argument("--file", "-f", dest="file", required=True,
                           help="Path to the snippet markdown file")
    sp_sn_add.add_argument("--no-validate", dest="no_validate", action="store_true",
                           help="Skip validation gate; register as unverified (required for mutates)")

    sp_sn_use = sn_sub.add_parser("use", parents=[shared], help="Run a snippet")
    sp_sn_use.add_argument("snippet_id")
    sp_sn_use.add_argument("--args", dest="snippet_args", default=None,
                           help="JSON object of arg values")
    sp_sn_use.add_argument("--dry-run", dest="dry_run", action="store_true",
                           help="Print the wrapped submission without executing")

    sp_sn_list = sn_sub.add_parser("list", parents=[shared], help="List snippets")
    sp_sn_list.add_argument("--include-deprecated", dest="include_deprecated",
                            action="store_true")
    sp_sn_list.add_argument("--safety", choices=["read-only", "mutates"], default=None)
    sp_sn_list.add_argument("--sort", choices=["hot", "cold", "recent"], default=None)

    sp_sn_show = sn_sub.add_parser("show", parents=[shared],
                                   help="Show a snippet body and metadata")
    sp_sn_show.add_argument("snippet_id")

    sp_sn_search = sn_sub.add_parser("search", parents=[shared],
                                      help="Search snippet library")
    sp_sn_search.add_argument("query", help="Free-text query")
    sp_sn_search.add_argument("--top", type=int, default=5)

    sp_sn_update = sn_sub.add_parser("update", parents=[shared],
                                      help="Update an existing snippet")
    sp_sn_update.add_argument("snippet_id")
    sp_sn_update.add_argument("--file", "-f", dest="file", default=None,
                              help="Replace the snippet body (re-runs validation gate)")
    sp_sn_update.add_argument("--set", dest="set_field", action="append", default=[],
                              metavar="key=value",
                              help="Update a metadata-only field (summary or arg description). "
                                   "Repeat for multiple. Cannot change args/example/safety/expected/body.")
    sp_sn_update.add_argument("--no-validate", dest="no_validate", action="store_true")

    sp_sn_dep = sn_sub.add_parser("deprecate", parents=[shared],
                                   help="Deprecate a snippet")
    sp_sn_dep.add_argument("snippet_id")
    sp_sn_dep.add_argument("--reason", default=None)
    sp_sn_dep.add_argument("--supersede", default=None,
                           help="Id of a snippet that replaces this one")

    sp_sn_prune = sn_sub.add_parser("prune", parents=[shared],
                                     help="Clean up the snippet library")
    sp_sn_prune.add_argument("--cold", action="store_true",
                             help="Also mark cold snippets as deprecated (opt-in)")
    sp_sn_prune.add_argument("--remove", action="store_true",
                             help="Hard-delete deprecated snippets older than --max-age-days")
    sp_sn_prune.add_argument("--max-age-days", type=int, default=30, dest="max_age_days")
    sp_sn_prune.add_argument("--dry-run", dest="dry_run", action="store_true")

    sp_sn_stats = sn_sub.add_parser("stats", parents=[shared],
                                     help="Show usage stats")
    sp_sn_stats.add_argument("--id", dest="snippet_id", default=None,
                             help="Show stats for a single snippet (default: all)")

    sp_sn_doc = sn_sub.add_parser("doctor", parents=[shared],
                                   help="Library health check (anti-rot audit)")
    sp_sn_doc.add_argument("--revalidate", action="store_true",
                           help="Re-run the validation gate on live read-only "
                                "snippets against the running Unity; refreshes "
                                "verified_at on passes (never touches usage stats)")

    args = p.parse_args()

    # Apply defaults for any shared arg the user didn't pass (SUPPRESS leaves
    # attr unset).  This restores the original UX while preventing subparser
    # overwrites of values given at the top level.
    for k, v in (("project", None), ("ip", "127.0.0.1"), ("port", None),
                 ("mode", "editor"), ("compile_ip", None), ("compile_port", None),
                 ("timeout", 30), ("as_json", False), ("verbose", False)):
        if not hasattr(args, k):
            setattr(args, k, v)

    # Resolve `code` from --file for exec
    if args.cmd == "exec":
        file = getattr(args, "file", None)
        if file is not None:
            if args.code is not None:
                p.error("argument --file: not allowed with positional code")
            try:
                args.code = Path(file).read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as e:
                p.error(f"--file: {e}")
            if not args.code.strip():
                p.error(f"--file: {file} is empty")
        elif args.code is None:
            p.error("missing C# code: provide it inline or via --file")

    root = find_project_root(args.project)
    # agent_root keys the per-project package-path cache; derive it from the
    # resolved project root (never raw cwd) so it stays stable across agents/cwd.
    agent_root = str(root) if root else (args.project or str(Path.cwd()))

    # Auto-detect editor port from refresh_state.json when needed for
    # --port (editor mode) or --compile-port fallback (runtime mode).
    detected_editor_port = None
    needs_detect = args.port is None or (args.mode == "runtime" and args.compile_port is None)
    if root and needs_detect:
        detected_editor_port = detect_port(root)
    default_port = DEFAULT_RUNTIME_PORT if args.mode == "runtime" else DEFAULT_EDITOR_PORT
    if args.port is None:
        if args.mode != "runtime" and detected_editor_port:
            args.port = detected_editor_port
        else:
            args.port = default_port
    # In runtime mode, compile/refresh/health still target the editor.
    if args.mode == "runtime" and args.compile_port is None:
        args.compile_port = detected_editor_port or DEFAULT_EDITOR_PORT

    # Validate --wait range
    if hasattr(args, "wait") and args.wait is not None:
        if args.wait < 0:
            print("Error: --wait timeout must be non-negative.", file=sys.stderr)
            sys.exit(1)
        if args.wait > 600:
            print(f"Warning: --wait capped to 600s (requested {args.wait}s)", file=sys.stderr)
            args.wait = 600

    # Pre-setup commands (work without the Unity package installed).
    if args.cmd == "setup":
        sys.exit(cmd_setup(root, args))
    if args.cmd == "status":
        sys.exit(cmd_status(root, args, agent_root))
    if args.cmd == "catalog":
        if root is None:
            print("Error: no Unity project found.", file=sys.stderr)
            sys.exit(1)
        if args.catalog_cmd == "sync":
            sys.exit(cmd_catalog_sync(root, args, agent_root))
        elif args.catalog_cmd == "list":
            sys.exit(cmd_catalog_list(root, args))
        else:
            sp_cat.print_help()
            sys.exit(1)
    if args.cmd == "snippets":
        if root is None:
            print("Error: no Unity project found.", file=sys.stderr)
            sys.exit(1)
        from cli.snippets.stats import SnippetDataError
        try:
            if args.snippets_cmd == "add":
                rc = cmd_snippets_add(root, args, agent_root)
            elif args.snippets_cmd == "use":
                rc = cmd_snippets_use(root, args, agent_root)
            elif args.snippets_cmd == "list":
                rc = cmd_snippets_list(root, args)
            elif args.snippets_cmd == "show":
                rc = cmd_snippets_show(root, args)
            elif args.snippets_cmd == "search":
                rc = cmd_snippets_search(root, args)
            elif args.snippets_cmd == "update":
                rc = cmd_snippets_update(root, args, agent_root)
            elif args.snippets_cmd == "deprecate":
                rc = cmd_snippets_deprecate(root, args)
            elif args.snippets_cmd == "prune":
                rc = cmd_snippets_prune(root, args)
            elif args.snippets_cmd == "stats":
                rc = cmd_snippets_stats(root, args)
            elif args.snippets_cmd == "doctor":
                rc = cmd_snippets_doctor(root, args, agent_root)
            else:
                sp_sn.print_help()
                rc = 1
        except SnippetDataError as e:
            # Corrupt committed audit — fail closed instead of overwriting it.
            _print_envelope(
                {"ok": False, "exitCode": 1, "summary": str(e)},
                args.as_json,
            )
            rc = 1
        sys.exit(rc)
    if not args.cmd:
        p.print_help()
        sys.exit(1)

    # Post-setup commands
    if root is None:
        print("Error: no Unity project found. Use --project to specify the path.", file=sys.stderr)
        sys.exit(1)

    from cli.core_bridge import find_package_dir
    pkg_dir = find_package_dir(root, agent_root)
    if pkg_dir is None:
        print("Error: C# Console package not found. Run 'cs setup' (or the unity-cli-setup skill) first.", file=sys.stderr)
        sys.exit(1)

    s = _new_session(root, args, pkg_dir)

    def _refresh():
        r = s.refresh(
            exit_playmode=getattr(args, "exit_playmode", False),
            changed_files=getattr(args, "files", None),
        )
        if args.wait is not None:
            if r.get("ok"):
                r = s.wait_ready(timeout=args.wait)
            else:
                print("Warning: refresh returned ok=false; --wait skipped", file=sys.stderr)
        return r

    def _list_commands_filtered(session, a):
        r = session.list_commands()
        return _filter_commands_by_type(r, a.cmd_type)

    dispatch = {
        "exec":     lambda: s.exec(args.code),
        "command":  lambda: s.command(args.namespace, args.action, args.args),
        "health":   lambda: s.health(),
        "refresh":  _refresh,
        "list-commands": lambda: _list_commands_filtered(s, args),
        "complete": lambda: s.complete(args.code, args.cursor),
        "batch":    lambda: s.batch(args.commands, args.stop_on_error),
    }

    result = dispatch[args.cmd]()

    if args.as_json:
        if not args.verbose:
            result = _slim_result(result)
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        s.emit(result)

    sys.exit(result.get("exitCode", 0))


if __name__ == "__main__":
    main()
