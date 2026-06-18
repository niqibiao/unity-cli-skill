# AGENTS.md

Guidance for AI coding agents — primarily the **Codex CLI** and other
non–Claude-Code agents — working in this repository. Claude Code reads
`CLAUDE.md`; the two are kept in sync on the essentials. This file adds the
cross-agent CLI invocation rule.

## Project overview

A dual-agent plugin providing a thin, pure-stdlib Python CLI for driving the
Unity Editor/Player through the C# Console HTTP service
(`com.zh1zh1.csharpconsole`). No external dependencies, no build step, no tests.

## Invoking the CLI (read this first)

Every skill calls the CLI at one stable, agent-agnostic path — run it
**verbatim, without changing directory**:

```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" <cmd> --project "$(pwd)"
```

Why a `$HOME` path and not the plugin directory? Codex does not expand a
plugin-root variable in skill-body shells, and it `cd`s into the skill directory
when it resolves a relative path — which would corrupt `--project "$(pwd)"`. A
literal `$HOME` path is the only form that resolves in both agents' shells with
no `cd` and no model path-reasoning. See `docs/dual-agent-support.md` for the
full derivation.

On first use the stable copy won't exist yet. The **unity-cli-setup** skill is
the single bootstrap entry point — run it (or `cs setup`, which auto-runs the
internal bootstrap). The bootstrap self-locates the bundled `cli/` and copies it
(plus the plugin manifest) to `$HOME/.unity-cli-plugin/current/`, recording the
source path and a content fingerprint in `.source.json`. It is idempotent, and
after a plugin upgrade the stable copy detects the changed source on its next run
and re-copies itself automatically (re-exec) — no manual refresh needed. See
`docs/dual-agent-support.md` for the mechanism.

## Command-first principle

When a built-in framework command exists, prefer `cs command <ns> <action>` over
`cs exec <code>`. Code execution is a fallback. Use `cs list-commands --json` to
discover commands; for reusable C#, prefer the snippet library (`cs snippets`).

## Two-phase lifecycle

- **Pre-setup:** only `setup` and `status` work (pure stdlib; `setup` also runs
  the internal CLI bootstrap).
- **Post-setup:** the full CLI is available once `com.zh1zh1.csharpconsole` is
  installed and Unity resolves it.

## Skills (no slash commands)

Everything ships as a skill (`skills/*/SKILL.md`), loaded by both Claude Code and
Codex — there are no slash commands (Codex never loads `commands/`). The
`unity-cli-setup` skill is the cross-agent setup entry point.

## Conventions

- Pure stdlib Python; do not add external dependencies or a build step.
- Project detection walks up from cwd for an `Assets/` directory; `--project`
  overrides.
- Never `git push` without explicit user confirmation.
- Keep `version` in lockstep across `.claude-plugin/plugin.json`,
  `.codex-plugin/plugin.json`, and `.claude-plugin/marketplace.json`.
