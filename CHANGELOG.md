# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

When bumping the version, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`
and start a fresh `## [Unreleased]` block above it. The release workflow extracts
the section matching the pushed tag (without the leading `v`) as release notes.

## [Unreleased]

### Changed

- **Version-namespaced CLI store + dispatch shim.** The stable cross-agent path
  (`$HOME/.unity-cli-plugin/current/cli/cs.py`) is now a tiny dispatch shim
  instead of one full copy of the CLI. Each plugin version is deposited under
  `$HOME/.unity-cli-plugin/store/<version>/cli`, and on every call the shim
  resolves which version the current project wants — its `.unity-cli/cli.json`
  pin (written by `setup`), else the major.minor match, else the just-bootstrapped
  version, else the newest — and runs it in-process via `runpy`. This lets
  multiple plugin versions coexist on one machine; previously every version copied
  into the same fixed directory, so two projects needing different versions
  clobbered each other.

### Added

- `cs install-cli --gc` prunes redundant store versions, keeping the newest patch
  of each major.minor line plus the just-bootstrapped (`.pending`) version.

### Fixed

- Self-refresh no longer hijacks a pinned older store version onto a newer source.
  Store entries are immutable per-version snapshots: a `store/<version>` entry now
  re-syncs from its source only while the source is still that same version with
  changed content (the dev-edit loop), never across versions.
- `setup` can upgrade an already-pinned project again. When a newer version was
  just bootstrapped (`.pending`), the shim now dispatches `setup` to that version
  rather than the project's old exact pin — so `setup --update` after a plugin
  upgrade actually re-pins the project (and resolves the new package tag) instead
  of being trapped on the old version. Non-setup commands still honor the pin.
- The shim now resolves the Unity project root (mirroring `find_project_root`)
  before reading the per-project pin, so a `--project` pointing at a subdirectory
  (e.g. `Assets/`) or at a parent that contains the project still finds the pin
  `setup` wrote at the root — instead of missing it and dispatching `.pending`/newest.
- `cs setup` no longer pins the project on the "already installed" no-op. The pin
  now happens only when setup actually installs or updates the package, so running
  setup after a plugin upgrade without `--update` (or declining the mismatch
  prompt) no longer strands the project on the newer CLI while Unity still has the
  old package/protocol.
- `.pending` is preserved across setups instead of being consumed by the first
  one, so on a machine with several projects pinned to an older version, each
  project's `setup --update` still picks up the freshly bootstrapped version
  (`.pending` is overwritten by the next `install-cli`).
- The shim routes `install-cli` (store-level maintenance, e.g. `install-cli --gc`)
  to the newest store entry instead of the project's pinned CLI, so it is never
  dispatched to an older entry that predates newer flags like `--gc`.
- A project that already has the package but no pin (set up before pinning
  existed) now receives a migration pin on an "already installed" `setup` — but
  only when its installed package is aligned with the running CLI — so it isn't
  left to drift to `.pending`/newest. Mismatched or already-pinned projects are
  left untouched.

## [1.5.2] - 2026-06-18

### Fixed

- **Codex marketplace install.** The installable plugin now ships in a `plugin/`
  subdirectory, with the repo-root `marketplace.json` pointing at it
  (`source: "./plugin"`). Codex rejects a plugin whose marketplace source is the
  marketplace root itself (`source: "./"` enumerates zero plugins and
  `codex plugin add` fails with "plugin not found"), so
  `codex plugin marketplace add niqibiao/unity-cli-plugin` +
  `codex plugin add unity-cli-plugin@unity-cli-plugin` could not work before.
  Claude Code consumes the same subdir-sourced marketplace unchanged. The team
  version-pin example switches to a `git-subdir` source (`path: "plugin"`).

## [1.5.1] - 2026-06-18

### Added

- Dual-agent support (Claude Code + Codex CLI) from a single bundle. Skills now
  invoke the CLI by one stable, agent-agnostic path
  (`$HOME/.unity-cli-plugin/current/cli/cs.py`); an internal bootstrap copies the
  CLI (and the plugin manifest) there from wherever the plugin is
  installed, so it works identically under both agents with no per-command path
  notes and `--project "$(pwd)"` always intact. The stable copy records its
  source path + a content fingerprint and **self-refreshes**: after a plugin
  upgrade (or dev edit) it detects the changed source on the next run and
  re-copies itself (then re-execs), so no manual refresh is needed. Adds a
  `.codex-plugin/plugin.json` manifest, a cross-agent `unity-cli-setup` skill
  (Codex has no slash commands) that is the sole bootstrap entry point, and an
  `AGENTS.md` contributor guide. `cs setup` auto-runs the bootstrap. See
  `docs/dual-agent-support.md`.
- All slash commands converted to skills (`unity-cli-setup`, `unity-cli-status`,
  `unity-cli-refresh`, `unity-cli-refresh-commands`, `unity-cli-sync-catalog`), so
  every entry point works in both Claude Code and Codex. The `commands/` directory
  is removed; there are no more `/unity-cli-*` slash commands (Claude Code triggers
  the skills by intent).

### Fixed

- The package cache (`_save_cache` / `_save_catalog_cache`) no longer crashes
  when the plugin directory is read-only (e.g. an agent's plugin cache) — the
  cache is only an optimization and now degrades silently instead of raising on
  nearly every command.

## [1.5.0] - 2026-06-13

### Added

- Self-evolving C# snippet library: `cs snippets list / show / search / use /
  add / update / deprecate / prune / stats`. Snippets are project-local markdown
  files at `.unity-cli/snippets~/<id>.md` containing a `static Run(...)` method;
  the CLI wraps each submission in a unique `static class __Snip_<hash>` for
  symbol isolation across REPL sessions. Validation gate runs each new snippet's
  `example` through the REPL (read-only auto-validated; mutates requires
  `--no-validate` and is recorded as unverified). Usage tracking auto-deprecates
  snippets after 5 consecutive failures spanning ≥ 7 days. Cold detection is
  informational only; `prune --cold` is opt-in.
- `unity-cli-snippets` skill: operator's manual for the snippet library, with
  hard decision order (command → snippet → ad-hoc) and distill criteria.
- `cs snippets doctor [--revalidate]`: anti-rot health check — integrity
  drift (orphan files, missing files, corrupt bodies), staleness (broken /
  cold / unverified), removal candidates, and opt-in live revalidation of
  read-only snippets to catch Unity API drift after upgrades. Paired with
  the `unity-cli-snippets-audit` skill (triage table; destructive cleanup
  always requires user confirmation).
- `cs setup` automatically adds `.unity-cli/snippets-stats.json` to the project
  `.gitignore` to avoid PR churn from routine usage tracking. The audit file
  (`snippets-audit.json`) remains committed as project state.

### Changed

- `cs --json` (slim mode) now parses `data.resultJson` automatically when the
  underlying response carries it as a JSON string. `cs list-commands --json`
  consumers should read `data.commands` directly (previously they had to
  `json.loads(data)` first). The old shape is still emitted under `--verbose`.

### Fixed

- `cs catalog sync` now reads `commandNamespace` and `arguments` from the
  wire response. Previously it looked for `namespace` and `args`, which the
  service does not emit — so every synced custom-command entry ended up with
  an empty namespace, a broken `id` like `".action"`, and an empty `args`
  list, and the next sync's diff would falsely flag all prior entries as
  removed. Both legacy field names are still accepted for forward
  compatibility.
- `cs list-commands --type {builtin,custom}` now actually filters when the
  underlying response carries `resultJson` as a parsed dict. Previously the
  filter wrote to `data.commands` but left `data.resultJson` unchanged, and
  `_slim_result` then surfaced the unfiltered `resultJson`, so all three
  `--type` values returned the same list.
- `/unity-cli-sync-catalog` description corrected: it audits the built-in
  tables in `unity-cli-command/SKILL.md` against the live Editor and is
  intended for plugin maintainers, not for refreshing the per-project custom
  command cache (use `/unity-cli-refresh-commands` for that).
- `cs exec --mode runtime` now actually runs on the player. Previously the
  CLI's `ConsoleSession.exec` unconditionally called `execute_editor_request`,
  so runtime-mode snippets were POSTed to the editor's `"editor"` endpoint
  (via `compile_ip:compile_port`) without `targetIP/targetPort`, silently
  executing in the local Editor instead of the player and ignoring `--ip`
  entirely. The exec path now mirrors the REPL: in runtime mode it calls
  `execute_runtime_request`, which POSTs to `"compile"` with
  `targetIP/targetPort` so the Editor compiles and forwards to the player.
  `command` / `batch` / `complete` continue to route through the editor by
  design (matching the REPL's behavior — most commands are editor-only).

## [1.4.3] - 2026-04-29

### Changed

- `cs setup` now pins the package to the latest `vMAJOR.MINOR.*` tag in the
  remote that matches the plugin's version, instead of writing a bare URL
  (which Unity resolved to HEAD of the default branch). This eliminates the
  drift that produced `plugin X.Y.x ≠ package X.Z.x` warnings shortly
  after a package release. Discovery uses `git ls-remote --tags`; on no
  match or network failure, setup falls back to HEAD with a one-line
  warning. Pass `--no-pin` to opt out, or `--source URL#tag` to pin
  explicitly.
- `cs setup --method local` now `git checkout`s the resolved tag in the
  local clone (fresh or existing). The clone ends in detached HEAD; if you
  intend to develop in the clone, run `git checkout main` afterward.

### Fixed

- `cs setup` no longer prints a misleading `Pinning to vX.Y.Z` line (and
  no longer hits the network) on no-op runs where the package is already
  installed and `--update` was not passed. Pin resolution is now lazy.
- Release workflow now passes `--title "vX.Y.Z"` to `gh release create` so
  the rendered release title is just the tag, not the GitHub web fallback
  of `{tag}: {commit subject}`.

## [1.4.2] - 2026-04-29

### Added

- `cs exec --file PATH` reads C# code from a file. Useful for long or
  multi-line snippets where shell quoting would otherwise be painful.
  UTF-8 BOM is stripped automatically (handles files saved by Visual
  Studio / Rider / Unity).
- Empty / unreadable files are rejected with a clean parser error
  instead of silently sending empty code to Roslyn.

### Fixed

- Shared flags (`--project`, `--ip`, `--port`, `--mode`, `--timeout`,
  `--json`, …) placed **before** the subcommand are no longer reset to
  their defaults by the subparser. Both `cs --project X status` and
  `cs status --project X` now behave the same.

### Workflow

- Release notes are sourced from this file. The `release.yml` workflow
  looks up the section matching the pushed tag and falls back to
  `--generate-notes` when no matching section is present.
- The Codex companion plugin now publishes its own GitHub Release for
  every `vX.Y.Z-codex` tag, mirroring the main release. Previously the
  `-codex` tag was created but no Release was attached, because tags
  pushed by `GITHUB_TOKEN` cannot trigger other workflows.
