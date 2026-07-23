# AGENTS.md

Guidance for AI coding agents — primarily the **Codex CLI** and other
non–Claude-Code agents — working in this repository. Claude Code reads
`CLAUDE.md`; the two are kept in sync on the essentials. This file adds the
cross-agent CLI invocation rule.

## Project overview

A pure **skill** (`unity-cli`) providing a thin, pure-stdlib Python CLI for driving
the Unity Editor/Player through the C# Console HTTP service
(`com.zh1zh1.csharpconsole`). No external dependencies, no build step. Installed with
`npx skills add niqibiao/unity-cli-skill --copy`; the CLI is bundled in the skill and
runs in place.

## Invoking the CLI (read this first)

Every skill calls the CLI at the skill's own base directory — run it **verbatim,
without changing directory**:

```bash
python "<SKILL_DIR>/scripts/cli/cs.py" <cmd> [--json]
```

`--json` only on `command` / `list-commands` / `batch` / `complete` (their payload is
emitted solely as JSON); every other subcommand prints an equivalent text form.

`<SKILL_DIR>` is the absolute base directory the agent provides when the skill loads.
Both Claude Code and Codex substitute it to an absolute path and run it **without
`cd`** (verified on Codex 0.139 under native skills).

**Do not pass `--project`.** `find_project_root()` auto-detects the Unity root by
walking up from the working directory and from the CLI's own committed location
(`__file__`), so it resolves the project regardless of the shell's cwd. `--project
<path>` is an optional override only.

The CLI runs in place from the committed skill. `cs setup` installs the Unity package: if
it's missing from `Packages/manifest.json`, setup adds the source (git URL by default,
`--source`/`--update` to override) and you open Unity to resolve it; when it's already
present, setup just warns on a CLI/package `major.minor` mismatch.

## Command-first principle

When a built-in framework command exists, prefer `cs command` over `cs exec`. Code
execution is a fallback. Use `cs list-commands --json` to discover commands; for reusable
C#, prefer the snippet library (`cs snippets`). C# code for `exec` goes in a raw `.cs`
file via `--file`; structured params for `command`/`batch`/`complete` go in a JSON file
via `--input` (never inline) — see SKILL.md's "Passing parameters".

## Two-phase lifecycle

- **Pre-setup:** `setup` and `status` work with pure stdlib, no Unity package needed.
- **Post-setup:** the full CLI is available once `com.zh1zh1.csharpconsole` is
  installed and Unity resolves it.

## Skills (no slash commands)

Everything ships in one skill, `skills/unity-cli/SKILL.md` (+ `references/*.md`),
loaded by both Claude Code and Codex — there are no slash commands. The `cs setup`
subcommand is the cross-agent setup/version-check entry point.

## Conventions

- Pure stdlib Python; do not add external dependencies or a build step.
- Project detection walks up from cwd, then from `__file__`, for an `Assets/` +
  `ProjectSettings/` root; `--project` overrides.
- Machine-local state lives in a per-project home cache, never the project tree.
- The version source is `skills/unity-cli/scripts/cli/VERSION`; keep it in lockstep
  with the git tag on release.
- Never `git push` without explicit user confirmation.
