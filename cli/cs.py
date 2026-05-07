"""Unity C# Console CLI — thin dispatcher over csharpconsole_core."""

import argparse
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

from cli import PACKAGE_NAME, DEFAULT_SOURCE, DEFAULT_EDITOR_PORT, DEFAULT_RUNTIME_PORT, save_pkg_path


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


def _clone_with_progress(source, dest, tag=None):
    """Clone a git repo, printing progress at 25% intervals.

    If *tag* is given, clones that tag specifically (shallow). The *source*
    must be a bare URL — strip any '#fragment' before calling.
    """
    if tag:
        print(f"Cloning {source} at {tag} (shallow)")
        cmd = ["git", "clone", "--depth", "1", "--branch", tag, "--progress",
               str(source), str(dest)]
    else:
        print(f"Cloning {source} (shallow)")
        cmd = ["git", "clone", "--depth", "1", "--progress",
               str(source), str(dest)]
    print("Connecting...", flush=True)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print("Error: git is not installed or not on PATH.", file=sys.stderr)
        return 1
    last_phase = None
    last_milestone = -1
    buf = ""
    while True:
        chunk = proc.stderr.read(1)
        if not chunk:
            break
        ch = chunk.decode("utf-8", errors="replace")
        if ch in ("\r", "\n"):
            m = _PROGRESS_RE.match(buf.strip())
            if m:
                phase, pct = m.group(1), int(m.group(2))
                milestone = pct // 25
                if phase != last_phase or milestone > last_milestone:
                    print(f"  {phase}: {pct}%")
                    last_phase = phase
                    last_milestone = milestone
            buf = ""
        else:
            buf += ch
    proc.wait()
    if proc.returncode != 0:
        print("Error: git clone failed. Ask the user to check network/proxy and retry manually.", file=sys.stderr)
        return proc.returncode
    print(f"Cloned to {dest}")
    return 0


def _pull_local(local_dir):
    """Run `git pull --ff-only` in *local_dir*. Returns 0 on success."""
    print(f"Checking for updates: {local_dir}")
    try:
        result = subprocess.run(
            ["git", "-C", str(local_dir), "pull", "--ff-only"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("Error: git is not installed or not on PATH.", file=sys.stderr)
        return 1
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        print("Error: git pull failed. The local clone may have diverged; "
              "delete it and re-run setup.", file=sys.stderr)
        return 1
    stdout = result.stdout.strip()
    if "Already up to date" in stdout or "Already up-to-date" in stdout:
        print(f"Already up to date (local): {local_dir}")
    else:
        print(f"Updated (local): {local_dir}")
    return 0


def _checkout_tag_in_local(local_dir, tag):
    """Fetch tags and checkout *tag* in the existing clone. Returns 0 on success."""
    print(f"Fetching tags: {local_dir}")
    try:
        fetch = subprocess.run(
            ["git", "-C", str(local_dir), "fetch", "--tags", "--quiet"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("Error: git is not installed or not on PATH.", file=sys.stderr)
        return 1
    if fetch.returncode != 0:
        if fetch.stderr:
            print(fetch.stderr.strip(), file=sys.stderr)
        print("Error: git fetch failed. Check network/proxy and retry.", file=sys.stderr)
        return 1

    head = subprocess.run(
        ["git", "-C", str(local_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    target = subprocess.run(
        ["git", "-C", str(local_dir), "rev-parse", f"{tag}^{{commit}}"],
        capture_output=True, text=True,
    )
    if head.returncode == 0 and target.returncode == 0 and head.stdout.strip() == target.stdout.strip():
        print(f"Already up to date (local, pinned to {tag}): {local_dir}")
        return 0

    co = subprocess.run(
        ["git", "-C", str(local_dir), "checkout", "--quiet", tag],
        capture_output=True, text=True,
    )
    if co.returncode != 0:
        if co.stderr:
            print(co.stderr.strip(), file=sys.stderr)
        print(f"Error: git checkout {tag} failed.", file=sys.stderr)
        return 1
    print(f"Updated (local) to {tag}: {local_dir}")
    return 0


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


def _resolve_pin(source, no_pin):
    """Decide what to write into the manifest and what tag to check out.

    Returns (effective_source, target_tag, message_or_None) where:
      - effective_source: the string to write to manifest (git method) or
        the URL portion to clone (local method always strips the fragment).
      - target_tag: tag name to use with `git clone --branch` / `git checkout`,
        or None if no tag was resolved.
      - message_or_None: a single line to print, or None for silence.
    """
    # User-supplied explicit pin (URL#tag) wins over both discovery and --no-pin.
    if "#" in source:
        frag = source.split("#", 1)[1]
        return source, frag, f"Using explicit pin: {frag}"

    if no_pin:
        return source, None, None

    from cli.version_check import (find_matching_tag, get_plugin_version,
                                   parse_semver)
    plugin_ver = get_plugin_version()
    if not plugin_ver or plugin_ver == "unknown":
        return source, None, "Warning: cannot determine plugin version \u2014 installing from HEAD"

    tag = find_matching_tag(source, plugin_ver)
    if tag:
        return f"{source}#{tag}", tag, f"Pinning to {tag} (matched plugin {plugin_ver})"

    sv = parse_semver(plugin_ver)
    label = f"v{sv[0]}.{sv[1]}.*" if sv else "matching"
    return source, None, f"Warning: no {label} tag found in {source} \u2014 installing from HEAD"


def _new_session(root, args, pkg_dir):
    from cli.core_bridge import ConsoleSession
    return ConsoleSession(root, args.ip, args.port, args.mode, args.timeout,
                          pkg_dir=pkg_dir,
                          compile_ip=args.compile_ip, compile_port=args.compile_port)


def cmd_setup(root, args, agent_root=None):
    if root is None:
        print("Error: no Unity project found. Use --project to specify the path.", file=sys.stderr)
        return 1
    manifest = root / "Packages" / "manifest.json"
    if not manifest.exists():
        print(f"Error: {manifest} not found.", file=sys.stderr)
        return 1

    data = json.loads(manifest.read_text("utf-8"))
    deps = data.setdefault("dependencies", {})

    raw_source = args.source or DEFAULT_SOURCE
    method = args.method or "git"

    # Resolve the pin lazily: paths that early-return (e.g. "already installed"
    # without --update) skip the network call and avoid printing a misleading
    # "Pinning to ..." line for an action that won't happen.
    _pin_cache = {"done": False, "eff": raw_source, "tag": None}
    def _get_pin():
        if not _pin_cache["done"]:
            eff, tag, msg = _resolve_pin(raw_source, getattr(args, "no_pin", False))
            if msg:
                print(msg)
            _pin_cache.update(eff=eff, tag=tag, done=True)
        return _pin_cache["eff"], _pin_cache["tag"]

    if method == "local":
        existing = deps.get(PACKAGE_NAME, "")
        if existing.startswith("file:"):
            local_dir = (manifest.parent / existing[len("file:"):]).resolve()
        else:
            # Use existing file: deps (outside Packages/) as reference path
            ref_parent = None
            for pkg, val in deps.items():
                if pkg == PACKAGE_NAME or not isinstance(val, str):
                    continue
                if not val.startswith("file:"):
                    continue
                rel = val[len("file:"):]
                if rel.startswith("Packages/") or rel.startswith("Packages\\"):
                    continue
                ref_parent = Path(rel).parent
                break
            if ref_parent is not None:
                local_dir = (manifest.parent / ref_parent / PACKAGE_NAME).resolve()
                print(f"Using reference path from existing local package: {ref_parent.as_posix()}/")
            else:
                local_dir = root / "Packages" / PACKAGE_NAME
        rel_path = Path(os.path.relpath(local_dir, manifest.parent)).as_posix()
        dep_value_local = f"file:{rel_path}"
        pkg_json = local_dir / "package.json"
        if pkg_json.is_file():
            if PACKAGE_NAME in deps and deps[PACKAGE_NAME] == dep_value_local:
                _, target_tag = _get_pin()
                if target_tag:
                    rc = _checkout_tag_in_local(local_dir, target_tag)
                else:
                    rc = _pull_local(local_dir)
                if rc != 0:
                    return rc
                _warn_version_mismatch(local_dir)
                return 0
            # Directory exists but manifest points elsewhere (e.g. git) — update below
        else:
            # Remove incomplete clone leftovers before retrying
            if local_dir.exists():
                import shutil
                shutil.rmtree(local_dir)
            local_dir.parent.mkdir(parents=True, exist_ok=True)
            _, target_tag = _get_pin()
            clone_url = raw_source.split("#", 1)[0]
            rc = _clone_with_progress(clone_url, local_dir, tag=target_tag)
            if rc != 0:
                return 1

        dep_value = dep_value_local
    else:
        if PACKAGE_NAME in deps:
            if not getattr(args, "update", False):
                print(f"Already installed: {PACKAGE_NAME}")
                try:
                    from cli.core_bridge import find_package_dir
                    pkg_dir = find_package_dir(root, agent_root)
                    if pkg_dir:
                        _warn_version_mismatch(pkg_dir)
                except Exception:
                    pass
                return 0
            # --update: remove and re-add to force Unity re-resolve
            print(f"Forcing re-resolve of {PACKAGE_NAME} ...")
            del deps[PACKAGE_NAME]
        effective_source, _ = _get_pin()
        dep_value = effective_source

    deps[PACKAGE_NAME] = dep_value
    manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"Added {PACKAGE_NAME} to {manifest}")
    # Cache the resolved package path for subsequent CLI commands
    if method == "local":
        save_pkg_path(agent_root, local_dir)
    print("Open Unity Editor to resolve the package, then run: cs status")
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
            print(f"\u26a0 plugin {pl} \u2260 package {kl} \u2014 run `cs check-update` for details")
    except Exception:
        pass  # version check is best-effort, never block status
    return 0


def cmd_check_update(root, args, agent_root=None):
    from cli.core_bridge import find_package_dir
    pkg_dir = find_package_dir(root, agent_root) if root else None

    if not pkg_dir:
        print("package: NOT FOUND (run 'cs setup' first)")
        return 1

    source = getattr(args, "source", None) or DEFAULT_SOURCE
    from cli.version_check import check_versions, parse_semver
    info = check_versions(pkg_dir, source, timeout=5)

    if args.as_json:
        json.dump({"ok": True, "exitCode": 0, **info}, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    print(f"plugin:    {info['plugin']}")
    print(f"package:   {info['package'] or 'unknown'}")
    print(f"remote:    {info['remote'] or 'unavailable (network error)'}")

    if info["aligned"]:
        pv = info["package"] or "?"
        sv = parse_semver(pv)
        label = f"{sv[0]}.{sv[1]}.x" if sv else pv
        print(f"alignment: \u2713 aligned ({label})")
    else:
        pv_s = parse_semver(info["plugin"])
        kv_s = parse_semver(info["package"])
        pl = f"{pv_s[0]}.{pv_s[1]}.x" if pv_s else info["plugin"]
        kl = f"{kv_s[0]}.{kv_s[1]}.x" if kv_s else info["package"]
        print(f"alignment: \u26a0 plugin {pl} \u2260 package {kl}")

    if info["updateAvailable"]:
        print(f"update:    \u26a0 package {info['package']} \u2192 {info['remote']} available")
        print("hint:      run `cs setup --update` to update the package")
    else:
        print(f"update:    \u2713 up to date")

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
    """Resolve where to write/read the catalog for this Unity project.

    Order of precedence:
    1. --catalog-path arg (and persist it)
    2. cached path from previous run
    3. interactive prompt (only if stdin is a TTY and not in --json mode)
    4. default: {project}/.unity-cli/catalog.json (and persist it)
    """
    from cli import (load_catalog_path, save_catalog_path,
                     default_catalog_path)

    explicit = getattr(args, "catalog_path", None)
    if explicit:
        cat_file = Path(explicit).expanduser().resolve()
        save_catalog_path(root, cat_file)
        return cat_file

    cached = load_catalog_path(root)
    if cached:
        return cached

    default = default_catalog_path(root)
    interactive = sys.stdin.isatty() and not args.as_json
    if interactive:
        prompt = (f"Where should the custom command catalog be stored?\n"
                  f"  [Enter to accept default: {default}]\n"
                  f"  Path: ")
        try:
            answer = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        chosen = Path(answer).expanduser().resolve() if answer else default
    else:
        chosen = default

    save_catalog_path(root, chosen)
    return chosen


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
    from cli import load_catalog_path

    explicit = getattr(args, "catalog_path", None)
    if explicit:
        cat_file = Path(explicit).expanduser().resolve()
    else:
        cat_file = load_catalog_path(root)

    if cat_file is None:
        msg = ("No catalog path configured for this project. "
               "Run 'cs catalog sync' first or pass --catalog-path.")
        if args.as_json:
            json.dump({"ok": False, "exitCode": 1, "summary": msg},
                      sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print(f"Error: {msg}", file=sys.stderr)
        return 1

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
    from cli.snippets.stats import init_audit_entry, init_stats_entry
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

    from cli.snippets.stats import _now
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
            from cli.snippets.stats import load_audit, save_audit, load_stats, save_stats
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

    snip = parse_snippet_file(text)

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
        _print_envelope(
            {"ok": True, "exitCode": 0, "summary": "dry run",
             "data": {"submission": submission}},
            args.as_json,
        )
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

    if response.get("ok") and response.get("exitCode", 0) == 0:
        record_success(root, args.snippet_id)
    else:
        record_failure(root, args.snippet_id)

    if args.as_json:
        json.dump(response, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        session.emit(response)
    return response.get("exitCode", 0)


def cmd_snippets_list(root, args):
    print("not yet implemented", file=sys.stderr)
    return 2


def cmd_snippets_show(root, args):
    print("not yet implemented", file=sys.stderr)
    return 2


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
    sub = p.add_subparsers(dest="cmd")

    sp_setup = sub.add_parser("setup", parents=[shared], help="Install Unity package")
    sp_setup.add_argument("--source", help="Git URL (default: GitHub repo)")
    sp_setup.add_argument("--method", choices=["local", "git"], default="git",
                          help="git = Unity resolves URL, local = clone to Packages/ (default: git)")
    sp_setup.add_argument("--update", action="store_true",
                          help="Update existing installation instead of skipping")
    sp_setup.add_argument("--no-pin", dest="no_pin", action="store_true",
                          help="Install from HEAD of the default branch instead of pinning to a tag matching the plugin major.minor")

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

    sub.add_parser("check-update", parents=[shared], help="Check version alignment and updates")

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

    agent_root = args.project or str(Path.cwd())
    root = find_project_root(args.project)

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

    # Pre-setup commands
    if args.cmd == "setup":
        sys.exit(cmd_setup(root, args, agent_root))
    if args.cmd == "status":
        sys.exit(cmd_status(root, args, agent_root))
    if args.cmd == "check-update":
        sys.exit(cmd_check_update(root, args, agent_root))
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
        if args.snippets_cmd == "add":
            sys.exit(cmd_snippets_add(root, args, agent_root))
        elif args.snippets_cmd == "use":
            sys.exit(cmd_snippets_use(root, args, agent_root))
        elif args.snippets_cmd == "list":
            sys.exit(cmd_snippets_list(root, args))
        elif args.snippets_cmd == "show":
            sys.exit(cmd_snippets_show(root, args))
        else:
            sp_sn.print_help()
            sys.exit(1)
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
        print("Error: C# Console package not found. Run 'cs setup' (or /unity-cli-setup) first.", file=sys.stderr)
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
