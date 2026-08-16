# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

When bumping the version, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`
and start a fresh `## [Unreleased]` block above it. The release workflow extracts
the section matching the pushed tag (without the leading `v`) as release notes.

## [Unreleased]

## [2.2.0] - 2026-08-16

### Added

- **`cs test`** — run Unity Test Framework tests (EditMode or PlayMode) through
  the new `editor/test.run`/`editor/test.status` package commands, poll until
  completion across play-mode transitions and domain reloads, and report
  structured pass/fail results (exit 0 pass, 3 failures, 4 outcome unknown).
  Requires `com.unity.test-framework` in the consuming project; without it the
  commands stay discoverable and return an explanatory error.
- **ScriptableObject authoring** — `scriptableobject/create`, `scriptableobject/get`,
  and `scriptableobject/modify` package commands in the `assets` domain for
  creating SO assets by type and reading/writing their serialized fields.

### Changed

- The authoring surface grows to 56 commands (61 package-owned built-ins with
  the 5 control-plane contracts); documented counts now match the registry.
- READMEs state the trusted-LAN service model explicitly: the Unity-side
  service binds all interfaces without authentication by design and must not
  be exposed to untrusted networks.

## [2.1.0] - 2026-08-15

### Added

- **`cs doctor`** — read-only reliability diagnosis with structured findings
  (project/package/version offline checks plus live service health), working
  before the Unity package is installed.
- **`cs wait-ready`** — poll until the editor service is genuinely ready
  (phase, compile/update flags, and the 2.1 main-thread heartbeat), with
  `--expect-operation`/`--min-generation` to bind the wait to one refresh
  operation and reject stale state from earlier generations.
- **Play-mode compile deferral detection** — when a waited refresh is deferred
  by the editor's Script Changes While Playing preference
  (`recompile_after_finished`), `cs refresh --wait` and `cs wait-ready` fail
  fast with `editor.play_mode_deferring_compile` instead of burning the
  timeout, and instruct the agent to ask the user before exiting play mode.

### Changed

- **`cs refresh --wait` readiness now binds to the triggered operation** — the
  wait tracks the returned operation id and generation rather than the first
  `ready` phase mirror, so a service-restart blip can no longer report ready
  while the requested compile is still pending.
- **`references/refresh.md` no longer recommends `--exit-playmode` by
  default** — plain `cs refresh --wait` is the first choice; exiting play mode
  requires the user's approval because it discards runtime state.
- **CLI version moves to 2.1.0** to stay on the same compatibility line as the
  Unity package's 2.1.0 health-field additions.

## [2.0.8] - 2026-07-23

### Removed

- **`cs complete` and the `/completion` bridge** — unity-cli no longer exposes the
  interactive completion endpoint. Agent automation submits complete C# and had no
  internal consumer for completion candidates; the Unity package may continue to
  provide `/completion` for its own interactive clients.

### Changed

- **Cross-agent repository guidance now has one source of truth** — `AGENTS.md`
  contains the shared CLI, architecture, test, setup-safety, and release rules;
  `CLAUDE.md` imports it with `@AGENTS.md` instead of maintaining a drifting copy.
- **REPL behavior documentation synced with the Unity package** —
  `references/exec-code.md` now explains that only successfully compiled `using`
  directives persist, that ambiguous internal compatibility types can make one
  submission fall back to standard C# accessibility, and how to respond to
  `[REPL NOTICE]` / `[REPL ACTION REQUIRED]`. Those upstream behavior changes did
  not alter endpoint paths or request/response contracts.

## [2.0.7] - 2026-07-23

### Fixed

- **Cross-call REPL context is now available on demand** — `--session <id>` is
  forwarded through `cs.py` and `core_bridge.py` to the C# Console request UUID, so
  dependent `cs exec` calls can intentionally share variables, `using`s, types, and
  helpers. Omitting the option keeps the previous fresh-session default; unrelated
  agents/tasks use different ids, and domain reload still clears all session state.
  The skill now tells agents to opt in only for genuinely dependent calls instead of
  claiming state is either always shared or never shareable.
- **`cs status` text mode now exits like the JSON branch** — 0 only when the live
  service is healthy; no project / package missing / service unreachable all exit 1
  (previously the text branch returned 0 for everything but a missing project),
  matching the new "text mode: exit code carries success" convention. When the
  service is down, text mode now also prints the installed package version read
  from disk (`version: X.Y.Z (package on disk)`) instead of omitting the version
  line entirely.

### Changed

- **`--json` is no longer blanket-required** (docs only, CLI unchanged) — the skill now
  asks for `--json` only on the four commands whose payload is emitted solely as
  structured JSON (`command`, `list-commands`, `batch`, `complete`). All other
  subcommands print an equivalent, cheaper text form and run without it; `exec` adds
  `--json` only when the structured envelope is needed. (`cs setup` never had a JSON
  branch — its documented `--json` was silently ignored.)
- **C# code goes through `--file`, never JSON-wrapped** — `SKILL.md` and
  `references/exec-code.md` now make a raw `.cs` file the only documented way to pass
  code to `cs exec`; the `--input '{"code": …}'` form (every quote/backslash/newline
  JSON-escaped) is demoted to a stdin-piping edge case. Scratch `.cs` / `req.json`
  files now have a **mandatory location**: the absolute path
  `<project-root>/Temp/CSharpConsole/AgentScratch/` — inside Unity's own `Temp/`
  (never imported, auto-cleaned on editor close, write-sandbox-friendly for
  workspace-bound agents, and colocated with the service's existing
  `Temp/CSharpConsole/` state). Semantic file names, overwritten per task; one-shot
  suffix only under known same-project concurrency. Never under `Assets/` (a typical
  REPL snippet fails project compilation there — blocking refresh workflows and,
  after an editor restart, potentially preventing the service from starting), and
  never delete `Temp/CSharpConsole/` itself. Decided in an adversarial CC↔Codex
  review (local audit transcript:
  `cc-codex-discussion-history/20260722-213810-scratch-file-conventions.md`, kept
  untracked by repo policy), which replaced the earlier
  `<user-temp>/csharpconsole/<session>/` draft: a user-temp root is not guaranteed
  writable under workspace-bound agent sandboxes, and the session token was pure
  bookkeeping.
- **First-time setup asks which source shape** (`references/setup.md`) — before writing
  the manifest the agent now offers an explicit choice: pinned git URL (default,
  team-friendly) vs. cloning the package to a user-chosen local path and installing via
  `--source file:<dir>` (agent performs the clone; caveat documented that absolute
  `file:` paths in the committed manifest are machine-specific).

## [2.0.3] - 2026-07-11

### Fixed

- **`core_bridge` retry survives the urllib core** — the domain-reload retry wrapper
  (`_make_post_with_retry`) caught only `OSError`, which the old `requests`-based core
  satisfied (its exceptions subclass `OSError`) but the new stdlib-`urllib` core does
  not: it converts every transport failure — connection refused, timeout, non-2xx —
  into `TransportError(Exception)`. The wrapper now also catches the core's
  `TransportError`, falling back to `OSError`-only against a pre-`TransportError` core,
  restoring the retry-once behavior when the service briefly refuses connections during
  a Unity domain reload. The CLI itself has no `requests` usage or HTTP layer to change.

## [2.0.2] - 2026-07-10

### Added

- **`cs setup` pins the git source to a `major.minor`-matched tag** — a plain git URL
  (no `#fragment`) is written to `Packages/manifest.json` as `…git#vX.Y.Z`, resolved to
  the newest upstream tag on the CLI's `major.minor` line, so the manifest records the
  intended version instead of leaving only a commit hash in `packages-lock.json`. `file:`
  paths and URLs already carrying a `#fragment` are written as-is; on a failed tag query
  (offline) setup falls back to the unpinned URL with a warning.

### Changed

- **Consent before writing the manifest** (`SKILL.md`, `references/setup.md`) — `cs setup`
  and `--update` write the shared, version-controlled `Packages/manifest.json`; the docs
  now require stating what will be written and getting the user's go-ahead first. The
  read-only commands (`cs status`, `cs health`) never need consent.

## [2.0.1] - 2026-06-25

### Changed

- **Quick Start rewritten** (`README.md`, `README_zh.md`) — collapsed the verbose
  warning block + 3-step bash walkthrough into a single `npx skills add` install, a
  highlighted callout that the install scope must be the Unity project (not home /
  global), and a one-line reason (the bundled CLI locates the project by walking up
  from its own file location).
- **Agent-facing setup guidance** (`SKILL.md`, `references/setup.md`) — `--json` and
  the expanded `python … cs.py …` command line are marked agent-internal; the
  user-facing verify step is plain-language "check unity-cli status", never the raw
  command.

## [2.0.0] - 2026-06-25

### Changed

- **BREAKING — pure-skills distribution.** Installed with
  `npx skills add niqibiao/unity-cli-skill --copy`, not as a Claude Code / Codex
  marketplace plugin. The CLI is bundled under `skills/unity-cli/scripts/cli/` and
  runs in place — no `~/.unity-cli-plugin` store/shim/copy. The 9 skills are merged
  into one `SKILL.md` + `references/`.
- **BREAKING — no version management.** Removed per-project CLI version dispatch
  (store/shim/pin), package-tag auto-pin, `install-cli`, `check-update`, and
  self-refresh. The committed copy is the version record; a runtime
  `⚠ version mismatch` warning is the only compatibility check (CLI ↔ package
  `major.minor`).
- **BREAKING — params via `--input` JSON only.** `cs command` / `exec` / `batch` /
  `complete` and `cs snippets use` take their params as a single JSON object from a
  file (or `-` for stdin) via `--input`; inline positional args and `--args` were
  removed. This eliminates cross-shell quoting/escaping of C# code and nested JSON.
  `exec` also accepts `--file` for raw C#.
- **`cs setup` installs the package.** When `com.zh1zh1.csharpconsole` is absent from
  `Packages/manifest.json`, setup adds the source (git URL by default; `--source` to
  override, `--update` to force re-resolve) and you open Unity to resolve it — the
  source is written as-is, no version pin. When already present, setup is a no-op
  that version-checks.
- **Runtime port auto-detection.** In `--mode runtime`, an omitted `--port` probes
  `15500-15509` to find the in-player service (players don't write `refresh_state.json`).
- The CLI auto-detects the Unity project (walk-up from cwd + the CLI's committed
  location); `--project` is now an optional override, not required.
- Machine-local state (package-path cache, snippet usage stats) moved out of the
  project into a per-project home cache (`%LOCALAPPDATA%\unity-cli\<key>\` /
  `$XDG_CACHE_HOME/unity-cli/<key>/`); the project tree stays clean.

### Migration

- Old `~/.unity-cli-plugin/` (store + shim) and any `<project>/.unity-cli/cli.json`
  pins are now dead data — safe to delete. Reinstall with
  `npx skills add niqibiao/unity-cli-skill --copy`.

## [1.5.3] - 2026-06-23

### Added

- **Version-namespaced CLI store + dispatch shim** — multiple plugin versions can
  coexist on one machine, each Unity project running the CLI version it was set up
  with. Each plugin version is deposited under
  `$HOME/.unity-cli-plugin/store/<version>/cli`; the fixed cross-agent path
  (`$HOME/.unity-cli-plugin/current/cli/cs.py`) is a tiny stdlib dispatch shim that
  runs the right one in-process via `runpy`:
  - a command runs the project's **pinned** version **verbatim**
    (`<project>/.unity-cli/cli.json`, written by `setup`);
  - with **no usable pin** (unpinned/legacy project, or a pin whose version isn't
    installed) it runs the **optimal** version — the store CLI matching the
    project's installed Unity package (`major.minor`, highest patch), else the
    newest — so the project just works instead of erroring. The installed package
    version is read from `Packages/packages-lock.json` first (authoritative),
    falling back to the manifest / embedded package / `PackageCache`; an ambiguous
    cache fails closed to the newest CLI rather than guessing by filesystem order;
  - `setup` / `install-cli` run the **newest** installed version.

  A **pinned project never drifts** — it changes only when the user re-runs
  `setup`. `setup` warns on a package/CLI version mismatch and the user decides
  (the `unity-cli-setup` skill prompts); the CLI never moves a version the user
  pinned. `setup` pins to the (newest) CLI it ran **only when that CLI is aligned
  with the package it installed**; a deliberate off-line install
  (`--source URL#vX.Y.Z` under a newer CLI) writes no pin and clears any stale one,
  letting the optimal pick run a compatible CLI. A failed store/shim write **fails
  `setup` before the project manifest is touched**, never half-succeeding.

### Fixed

- **Post-setup verify prompt regression.** Restored the `unity-cli-setup` skill's
  Verify step to point the user at the **unity-cli-status** skill (it had
  regressed into pasting a raw `cs.py status` one-liner during the command→skill
  refactor). Added guardrails: the agent no longer over-claims service
  reachability (`status` is the check, not a guarantee) and no longer invents CLI
  subcommands (raw C# is `cs exec`; there is no `cs run` — check `cs --help`
  when unsure).

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
