# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A **pure skill** (`unity-cli`) providing a thin Python CLI for interacting with Unity Editor/Player via the C# Console HTTP service (`com.zh1zh1.csharpconsole`). Pure stdlib Python — no external dependencies. Installed with `npx skills add niqibiao/unity-cli-skill --copy`; the CLI is bundled under `skills/unity-cli/scripts/cli/` and runs **in place** from the committed skill.

## CLI

Skills invoke the CLI by the skill's own base directory:

```
python "<SKILL_DIR>/scripts/cli/cs.py" <command> --json [args]
```

`<SKILL_DIR>` is the absolute base dir the agent provides when the skill loads (Claude Code and Codex both substitute it; both run it without `cd`). **Do not pass `--project`** — `find_project_root()` auto-detects the Unity root by walking up from the working directory and from the CLI's own committed location (`__file__`). `--project <path>` is an optional override only. Prefix with `PYTHONDONTWRITEBYTECODE=1` so running the CLI leaves no `__pycache__` in the project.

Shared flags: `--project <path>` (override), `--ip` (default 127.0.0.1), `--port` (default 14500), `--mode editor|runtime`, `--compile-ip` (runtime mode only), `--compile-port` (runtime mode only, default auto-detect), `--timeout` (default 30), `--json`

### Two-phase lifecycle

- **Pre-setup:** `setup` and `status` work with pure stdlib, no Unity package needed.
- **Post-setup:** full CLI available once `com.zh1zh1.csharpconsole` is installed in the project and Unity resolves it.

`setup` installs the package: if `com.zh1zh1.csharpconsole` is missing from `Packages/manifest.json`, it adds the source (git URL by default, `--source` to override; `--update` forces re-resolve) and tells you to open Unity to resolve it. When the package is already present, `setup` is a no-op that warns on a CLI/package `major.minor` mismatch. Every command also locates + caches the resolved package path lazily on first run, so `setup` is a convenience, not a gate.

### Command-first principle

When a built-in framework command exists, prefer `cs command` over `cs exec`. Code execution is a fallback, not the default. Use `cs list-commands --json` to discover available commands. Params for `command`/`exec`/`batch`/`complete` go in a JSON file via `--input` (never inline) — see the skill's "Passing parameters" note.

### Commands

| Command | Phase | Description |
|---------|-------|-------------|
| `cs setup` | pre | Install the package into the manifest (version-check if already present) |
| `cs status` | pre | Package + connection status + version info |
| `cs exec --input \| --file FILE` | post | Execute C# code (JSON params, or raw .cs file) |
| `cs command --input` | post | Run framework command (JSON params) |
| `cs batch --input [--stop-on-error]` | post | Execute multiple commands in one HTTP roundtrip |
| `cs health` | post | Service health check |
| `cs refresh [--wait TIMEOUT] [--exit-playmode]` | post | Trigger asset refresh + script compilation |
| `cs list-commands` | post | List available commands |
| `cs complete --input` | post | Get completions |
| `cs catalog sync [--catalog-path PATH]` | post | Sync custom command catalog from live editor |
| `cs catalog list` | post | List cached custom commands (offline) |
| `cs snippets list \| show \| search \| use` | post | Browse and run reusable C# snippets |
| `cs snippets add \| update \| deprecate \| prune \| stats` | post | Manage snippet library |
| `cs snippets doctor [--revalidate]` | post | Library health check / anti-rot audit |

## Architecture

```
Agent harness (Claude Code / Codex)
  └── Skill: skills/unity-cli/SKILL.md  (cs macro + routing → references/*.md)
       └── CLI: skills/unity-cli/scripts/cli/cs.py   (runs in place)
            └── core_bridge.py → dynamically imports csharpconsole_core from the Unity package
                 └── HTTP POST → Unity Editor/Player service (port 14500 editor / 15500 player)
```

### Dynamic bridge (`scripts/cli/core_bridge.py`)

The CLI does **not** bundle `csharpconsole_core`. It locates and imports it at runtime from the installed Unity package to guarantee version consistency. Resolution order:

1. `Packages/manifest.json` `file:` entry (resolves both default and custom local paths)
2. `Library/PackageCache/com.zh1zh1.csharpconsole@*/Editor/ExternalTool~/console-client/`

`ConsoleSession` is a facade that wires up the core modules (`client_base`, `command_protocol`, `config_base`, `output`, `response_parser`, `transport_http`) into one-liner methods: `exec()`, `command()`, `batch()`, `health()`, `complete()`, `list_commands()`, `refresh()`, `emit()`.

Connection errors are automatically retried once (1s delay) to handle transient failures during domain reload.

### Version & machine-local state

- **Version source:** `scripts/cli/VERSION` (a bare semver, read by `version_check.get_plugin_version`). `version_check` stays check-only — `_warn_version_mismatch` warns when the installed package and the CLI differ at `major.minor`; there is no dispatch or remote update check. Tag pinning lives in `cs.py`: when `cs setup` writes the manifest, `_pin_source_tag` pins a plain git URL to the newest upstream tag on the CLI's `major.minor` line (`file:`/`#fragment` sources and failed tag queries fall back to the unpinned source).
- **Shared constants** (`scripts/cli/__init__.py`): `PACKAGE_NAME`, `DEFAULT_SOURCE`, ports, and the package-path cache helpers.
- **Machine-local state** lives in a per-project home cache (`scripts/cli/paths.py`): `%LOCALAPPDATA%\unity-cli\<project-key>\` (Windows) / `$XDG_CACHE_HOME/unity-cli/<project-key>/` (else), keyed by a hash of the project root, written atomically. Holds the resolved package path (`pkg-dir`) and snippet usage stats — never the project tree.

### Skill structure

```
skills/unity-cli/SKILL.md            Single entry: cs macro + subcommand routing
skills/unity-cli/references/*.md     Per-topic docs (commands, exec-code, snippets,
                                       snippets-audit, refresh, catalog, status, setup)
skills/unity-cli/scripts/cli/        Bundled CLI (runs in place), incl. VERSION
skills/unity-cli/scripts/cli/cs.py   CLI dispatcher (argparse → handlers / ConsoleSession)
skills/unity-cli/scripts/cli/core_bridge.py  Dynamic import bridge + ConsoleSession facade
skills/unity-cli/scripts/cli/paths.py        Per-project home cache + atomic writes
```

### JSON result envelope

All post-setup commands return: `{ "ok": bool, "exitCode": int, "summary": str, "data": {...} }`

## Command Catalog

Built-in commands are statically documented in `skills/unity-cli/references/commands.md`. User-defined custom commands are cached per-project at `{project}/.unity-cli/catalog.json` (committed — shared with the team; pass `--catalog-path` to use a different location for one call). The agent reads this cache via `cs catalog list --json`. Run `cs catalog sync` after registering new C# commands. The maintainer audit (built-in tables vs the live Editor) also lives in `references/catalog.md`.

## Snippet Library

Self-evolving project-local library of reusable C# snippets executed via `cs exec` (no Unity compilation involvement). Snippet bodies live at `<project>/.unity-cli/snippets~/<id>.md`; the audit (`snippets-audit.json`) is committed, while usage **stats** are machine-local (in the home cache, not the project). The decision order is: built-in/custom command → snippet → ad-hoc `cs exec`.

See `references/snippets.md` for usage rules and `cs snippets --help` for the full CLI. Library maintenance (integrity, staleness, Unity API drift) is driven by `cs snippets doctor` — run `doctor --revalidate` after Unity version upgrades.

## Release Process

When bumping the version (e.g. on user request "bump to X.Y.Z and tag"), do **all** of the following in one commit before tagging — do not ask for clarification on the protocol:

1. **`CHANGELOG.md`** — rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` (use today's date). Insert a fresh empty `## [Unreleased]` block above it. The release workflow extracts this section verbatim as the GitHub Release body, so make sure pending entries already live under `[Unreleased]` before the bump.
2. **`skills/unity-cli/scripts/cli/VERSION`** — bump to `X.Y.Z` (bare semver, no `v`).
3. **Commit** with a `chore:` or `feat:` subject naming the version.
4. **`git tag vX.Y.Z`** locally; **never push without explicit user confirmation** (memory rule).

The `release.yml` workflow reads the matching CHANGELOG section and creates the `vX.Y.Z` release. Keep `VERSION` and the tag in lockstep (an optional CI step can assert `v$(cat VERSION)` == the tag).

## Development Notes

- **Always ask before pushing** — never `git push` without explicit user confirmation
- No build step, no external deps — just stdlib Python
- Unity project detection: `find_project_root()` walks up from cwd, then from `__file__` (the committed-in-project CLI), looking for an `Assets/` + `ProjectSettings/` root; `--project` overrides
- All entry points are in the one `unity-cli` skill; there are no slash commands. Skills call the CLI by `<SKILL_DIR>/scripts/cli/cs.py` so they work in both Claude Code and Codex
