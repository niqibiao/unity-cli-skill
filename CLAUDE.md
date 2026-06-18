# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Code plugin providing a thin Python CLI for interacting with Unity Editor/Player via the C# Console HTTP service (`com.zh1zh1.csharpconsole`). Pure stdlib Python — no external dependencies.

## CLI

Entry point (Claude Code): `python "${CLAUDE_PLUGIN_ROOT}/cli/cs.py" <command> [--json] [args]`

**Dual-agent (Claude Code + Codex):** skills invoke the CLI by one stable,
agent-agnostic path, `python "$HOME/.unity-cli-plugin/current/cli/cs.py" <command>`,
populated once by the internal bootstrap that `setup` runs (Codex can't expand
`${CLAUDE_PLUGIN_ROOT}` in skill-body shells). The stable copy self-refreshes when its source changes. Slash
commands stay Claude-Code-only and keep `${CLAUDE_PLUGIN_ROOT}`. See `AGENTS.md`
and `docs/dual-agent-support.md`.

Shared flags: `--project <path>`, `--ip` (default 127.0.0.1), `--port` (default 14500), `--mode editor|runtime`, `--compile-ip` (runtime mode only, default 127.0.0.1), `--compile-port` (runtime mode only, default auto-detect), `--timeout` (default 30), `--json`

### Two-phase lifecycle

- **Pre-setup:** only `setup` and `status` work (pure stdlib, no Unity package needed; `setup` also runs the internal CLI bootstrap)
- **Post-setup:** full CLI available after `com.zh1zh1.csharpconsole` is installed and Unity resolves it

### Command-first principle

When a built-in framework command exists, prefer `cs command <ns> <action>` over `cs exec <code>`. Code execution is a fallback, not the default. Use `cs list-commands --json` to discover available commands.

### Commands

| Command | Phase | Description |
|---------|-------|-------------|
| `cs setup [--source URL] [--update]` | pre | Add/update package in Packages/manifest.json (auto-bootstraps the stable `$HOME` CLI copy) |
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
  ├── Skills (plugin/skills/*/SKILL.md): unity-cli-{setup,status,refresh,refresh-commands,
  │     sync-catalog,command,exec-code,snippets,snippets-audit}
  └── CLI (plugin/cli/cs.py)
       └── core_bridge.py → dynamically imports csharpconsole_core from Unity package
            └── HTTP POST → Unity Editor/Player service (port 14500 editor / 15500 player)
```

### Dynamic bridge (`plugin/cli/core_bridge.py`)

The CLI does **not** bundle `csharpconsole_core`. It locates and imports it at runtime from the installed Unity package to guarantee version consistency. Resolution order:

1. `Packages/manifest.json` `file:` entry (resolves both default and custom local paths)
2. `Library/PackageCache/com.zh1zh1.csharpconsole@*/Editor/ExternalTool~/console-client/`

`ConsoleSession` is a facade that wires up the core modules (`client_base`, `command_protocol`, `config_base`, `output`, `response_parser`, `transport_http`) into one-liner methods: `exec()`, `command()`, `batch()`, `health()`, `complete()`, `list_commands()`, `refresh()`, `emit()`.

Connection errors are automatically retried once (1s delay) to handle transient failures during domain reload.

### Shared constants (`plugin/cli/__init__.py`)

`PACKAGE_NAME` and `DEFAULT_SOURCE` are defined once in `plugin/cli/__init__.py` and imported by both `cs.py` and `core_bridge.py`.

### Plugin structure

The installable plugin lives in the `plugin/` subdirectory; the repo root is a
**marketplace** that points at it (`source: "./plugin"`). The subdir layout is
required by Codex (a Codex marketplace cannot expose a plugin whose source is the
marketplace root itself — `source: "./"` is rejected); Claude Code consumes the
same subdir-sourced marketplace identically.

```
.claude-plugin/marketplace.json   Marketplace manifest (root; source → ./plugin)
plugin/.claude-plugin/plugin.json Plugin manifest (Claude Code)
plugin/.codex-plugin/plugin.json  Plugin manifest (Codex)
plugin/cli/__init__.py            Shared constants (PACKAGE_NAME, DEFAULT_SOURCE)
plugin/cli/cs.py                  CLI dispatcher (argparse → pre-setup handlers or ConsoleSession)
plugin/cli/core_bridge.py         Dynamic import bridge + ConsoleSession facade
plugin/skills/.../SKILL.md        Skill definition with trigger conditions and usage docs
```

### JSON result envelope

All post-setup commands return: `{ "ok": bool, "exitCode": int, "summary": str, "data": {...} }`

## Command Catalog

Built-in commands are statically documented in `plugin/skills/unity-cli-command/SKILL.md`.
User-defined custom commands are cached per-project as JSON (default `{project}/.unity-cli/catalog.json`; the path is remembered after the first sync and can be overridden via `cs catalog sync --catalog-path ...`). The agent reads this cache via `cs catalog list --json`.
Run the `unity-cli-refresh-commands` skill (i.e. `cs catalog sync`) after registering new C# commands to refresh the cache.
Run the `unity-cli-sync-catalog` skill (maintainer-only) to audit the built-in tables in `SKILL.md` against the live Editor and surface upstream additions/removals/signature changes.

## Snippet Library

Self-evolving project-local library of reusable C# snippets executed via `cs exec` (no Unity compilation involvement). Snippet bodies live at `<project>/.unity-cli/snippets~/<id>.md`; audit is committed, stats are gitignored. The plugin ships a `unity-cli-snippets` skill as the agent's operator manual; the skill instructs the agent to follow the decision order: built-in/custom command → snippet → ad-hoc `cs exec`.

See `plugin/skills/unity-cli-snippets/SKILL.md` for usage rules and `cs snippets --help` for the full CLI. Library maintenance (integrity, staleness, Unity API drift) is driven by `cs snippets doctor` via the `unity-cli-snippets-audit` skill — run `doctor --revalidate` after Unity version upgrades.

## Release Process

When bumping the version (e.g. on user request "bump to X.Y.Z and tag"), do
**all** of the following in one commit before tagging — do not ask for
clarification on the protocol:

1. **`CHANGELOG.md`** — rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`
   (use today's date). Insert a fresh empty `## [Unreleased]` block above it.
   The release workflow extracts this section verbatim as the GitHub Release
   body, so make sure pending entries already live under `[Unreleased]` before
   the bump (move stray notes if needed).
2. **`plugin/.claude-plugin/plugin.json`** — bump `version` field.
3. **`plugin/.codex-plugin/plugin.json`** — bump `version` (must match `plugin.json`).
4. **`.claude-plugin/marketplace.json`** — bump the matching `version` entry
   (all three manifests stay in lockstep at the same `major.minor`).
5. **Commit** with a `chore:` or `feat:` subject naming the version.
6. **`git tag vX.Y.Z`** locally; **never push without explicit user
   confirmation** (memory rule).

The `release.yml` workflow handles the rest: it reads the matching CHANGELOG
section, falls back to `--generate-notes` when no section is found, and creates
the `vX.Y.Z` release with a proper title.

## Development Notes

- **Always ask before pushing** — never `git push` without explicit user confirmation
- No build step, no tests, no external deps — just stdlib Python
- Unity project detection: walks up from cwd looking for an `Assets/` directory
- `find_project_root()` in `cs.py` handles project auto-detection; `--project` flag overrides
- All entry points are skills (`plugin/skills/*/SKILL.md`); there are no slash commands. Skills call the CLI by the stable `$HOME` path so they work in both Claude Code and Codex
