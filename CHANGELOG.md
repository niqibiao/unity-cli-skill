# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

When bumping the version, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`
and start a fresh `## [Unreleased]` block above it. The release workflow extracts
the section matching the pushed tag (without the leading `v`) as release notes.

## [Unreleased]

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
