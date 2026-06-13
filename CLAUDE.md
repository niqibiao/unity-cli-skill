# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Code plugin providing a thin Python CLI for interacting with Unity Editor/Player via the C# Console HTTP service (`com.zh1zh1.csharpconsole`). Pure stdlib Python — no external dependencies.

## CLI

Entry point: `python "${CLAUDE_PLUGIN_ROOT}/cli/cs.py" <command> [--json] [args]`

Shared flags: `--project <path>`, `--ip` (default 127.0.0.1), `--port` (default 14500), `--mode editor|runtime`, `--compile-ip` (runtime mode only, default 127.0.0.1), `--compile-port` (runtime mode only, default auto-detect), `--timeout` (default 30), `--json`

### Two-phase lifecycle

- **Pre-setup:** only `setup` and `status` work (pure stdlib, no Unity package needed)
- **Post-setup:** full CLI available after `com.zh1zh1.csharpconsole` is installed and Unity resolves it

### Command-first principle

When a built-in framework command exists, prefer `cs command <ns> <action>` over `cs exec <code>`. Code execution is a fallback, not the default. Use `cs list-commands --json` to discover available commands.

### Commands

| Command | Phase | Description |
|---------|-------|-------------|
| `cs setup [--source URL] [--update]` | pre | Add/update package in Packages/manifest.json |
| `cs status` | pre | Package + connection status + version info |
| `cs exec <code> \| --file FILE` | post | Execute C# code (inline or from file) |
| `cs command <ns> <action> [args]` | post | Run framework command |
| `cs batch <json-array> [--stop-on-error]` | post | Execute multiple commands in one HTTP roundtrip |
| `cs health` | post | Service health check |
| `cs refresh [--wait TIMEOUT] [--exit-playmode]` | post | Trigger asset refresh + script compilation |
| `cs list-commands` | post | List available commands |
| `cs complete <code> <cursor>` | post | Get completions |
| `cs check-update` | post | Version alignment + update check |
| `cs catalog sync` | post | Sync custom command catalog from live editor |
| `cs catalog list` | post | List cached custom commands (offline) |
| `cs snippets list \| show \| search \| use` | post | Browse and run reusable C# snippets |
| `cs snippets add \| update \| deprecate \| prune \| stats` | post | Manage snippet library |
| `cs snippets doctor [--revalidate]` | post | Library health check / anti-rot audit |

## Architecture

```
Claude Code harness
  ├── Skills (skills/unity-cli-command/, skills/unity-cli-exec-code/)
  ├── Slash commands (commands/*.md): /unity-cli-setup, /unity-cli-status, /unity-cli-refresh, /unity-cli-sync-catalog
  └── CLI (cli/cs.py)
       └── core_bridge.py → dynamically imports csharpconsole_core from Unity package
            └── HTTP POST → Unity Editor/Player service (port 14500 editor / 15500 player)
```

### Dynamic bridge (`cli/core_bridge.py`)

The CLI does **not** bundle `csharpconsole_core`. It locates and imports it at runtime from the installed Unity package to guarantee version consistency. Resolution order:

1. `Packages/manifest.json` `file:` entry (resolves both default and custom local paths)
2. `Library/PackageCache/com.zh1zh1.csharpconsole@*/Editor/ExternalTool~/console-client/`

`ConsoleSession` is a facade that wires up the core modules (`client_base`, `command_protocol`, `config_base`, `output`, `response_parser`, `transport_http`) into one-liner methods: `exec()`, `command()`, `batch()`, `health()`, `complete()`, `list_commands()`, `refresh()`, `emit()`.

Connection errors are automatically retried once (1s delay) to handle transient failures during domain reload.

### Shared constants (`cli/__init__.py`)

`PACKAGE_NAME` and `DEFAULT_SOURCE` are defined once in `cli/__init__.py` and imported by both `cs.py` and `core_bridge.py`.

### Plugin structure

```
.claude-plugin/plugin.json   Plugin manifest
cli/__init__.py              Shared constants (PACKAGE_NAME, DEFAULT_SOURCE)
cli/cs.py                    CLI dispatcher (argparse → pre-setup handlers or ConsoleSession)
cli/core_bridge.py           Dynamic import bridge + ConsoleSession facade
commands/*.md                Slash command definitions
skills/.../SKILL.md          Skill definition with trigger conditions and usage docs
```

### JSON result envelope

All post-setup commands return: `{ "ok": bool, "exitCode": int, "summary": str, "data": {...} }`

## Command Catalog

Built-in commands are statically documented in `skills/unity-cli-command/SKILL.md`.
User-defined custom commands are cached per-project as JSON (default `{project}/.unity-cli/catalog.json`; the path is remembered after the first sync and can be overridden via `cs catalog sync --catalog-path ...`). The agent reads this cache via `cs catalog list --json`.
Run `/unity-cli-refresh-commands` (i.e. `cs catalog sync`) after registering new C# commands to refresh the cache.
Run `/unity-cli-sync-catalog` (maintainer-only) to audit the built-in tables in `SKILL.md` against the live Editor and surface upstream additions/removals/signature changes.

## Snippet Library

Self-evolving project-local library of reusable C# snippets executed via `cs exec` (no Unity compilation involvement). Snippet bodies live at `<project>/.unity-cli/snippets~/<id>.md`; audit is committed, stats are gitignored. The plugin ships a `unity-cli-snippets` skill as the agent's operator manual; the skill instructs the agent to follow the decision order: built-in/custom command → snippet → ad-hoc `cs exec`.

See `skills/unity-cli-snippets/SKILL.md` for usage rules and `cs snippets --help` for the full CLI. Library maintenance (integrity, staleness, Unity API drift) is driven by `cs snippets doctor` via the `unity-cli-snippets-audit` skill — run `doctor --revalidate` after Unity version upgrades.

## Release Process

When bumping the version (e.g. on user request "bump to X.Y.Z and tag"), do
**all** of the following in one commit before tagging — do not ask for
clarification on the protocol:

1. **`CHANGELOG.md`** — rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`
   (use today's date). Insert a fresh empty `## [Unreleased]` block above it.
   The release workflow extracts this section verbatim as the GitHub Release
   body, so make sure pending entries already live under `[Unreleased]` before
   the bump (move stray notes if needed).
2. **`.claude-plugin/plugin.json`** — bump `version` field.
3. **`.claude-plugin/marketplace.json`** — bump the matching `version` entry
   (must stay in lockstep with `plugin.json`).
4. **Commit** with a `chore:` or `feat:` subject naming the version.
5. **`git tag vX.Y.Z`** locally; **never push without explicit user
   confirmation** (memory rule).

The `release.yml` and `convert-codex.yml` workflows handle the rest:
they read the matching CHANGELOG section, fall back to `--generate-notes`
when no section is found, and create matching `vX.Y.Z` and `vX.Y.Z-codex`
releases with proper titles.

## Development Notes

- **Always ask before pushing** — never `git push` without explicit user confirmation
- No build step, no tests, no external deps — just stdlib Python
- Unity project detection: walks up from cwd looking for an `Assets/` directory
- `find_project_root()` in `cs.py` handles project auto-detection; `--project` flag overrides
- Slash commands in `commands/` use `$ARGUMENTS` and `${CLAUDE_PLUGIN_ROOT}` template variables
