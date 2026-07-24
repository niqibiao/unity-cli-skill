# AGENTS.md

Cross-agent guidance for working in this repository. This is the canonical project
instruction file for Codex and other coding agents; `CLAUDE.md` imports it so shared
rules are maintained in one place.

## Project overview

`unity-cli` is a pure skill containing a thin, pure-stdlib Python CLI for driving the
Unity Editor/Player through the C# Console HTTP service
(`com.zh1zh1.csharpconsole`). There are no external dependencies or build step. It is
installed with `npx skills add niqibiao/unity-cli-skill --copy`; the bundled CLI runs
in place from `skills/unity-cli/scripts/cli/`.

## Invoking the CLI

Every skill invokes the CLI at the skill's own base directory. Run this form without
changing directory:

```bash
python "<SKILL_DIR>/scripts/cli/cs.py" <command> [args]
```

`<SKILL_DIR>` is the absolute base directory supplied when the skill loads. Do not
pass `--project` for normal use: `find_project_root()` walks up from both the current
working directory and the CLI's committed location (`__file__`) to find an `Assets/`
+ `ProjectSettings/` root. `--project <path>` is only an explicit override.

Use `--json` on `command`, `list-commands`, and `batch`, whose useful payload is
structured data. Other subcommands print an equivalent text form; add `--json` to
`exec` only when its structured envelope is specifically needed.

C# code for `exec` goes in a raw `.cs` file via `--file`. Structured parameters for
`command` and `batch` go in a JSON file via `--input`; never pass either inline.
Follow `skills/unity-cli/SKILL.md` for the mandatory scratch-file location and full
operational rules.

### Lifecycle and setup

- **Pre-setup:** `setup` and `status` use only the bundled stdlib CLI.
- **Post-setup:** the full CLI is available after
  `com.zh1zh1.csharpconsole` is installed and Unity resolves it.

`cs setup` writes the shared, version-controlled `Packages/manifest.json` when the
package is missing or `--update` is used. Explain the exact source and obtain user
approval before running a write-producing setup. When the package is already present,
setup only checks the CLI/package `major.minor` versions. Other commands locate and
cache the resolved package lazily, so setup is a convenience rather than a gate.

### Command-first principle

Prefer `cs command` whenever a built-in framework command covers the task. Use
`cs list-commands --offline --domain <domain> --tier core --json` to discover a
small committed contract set without connecting to Unity. Query the matching
advanced tier only when needed; remove `--offline` to verify the installed
package's live registry. Then check the snippet library (`cs snippets`) before
falling back to ad-hoc `cs exec`.

Recognized built-in requests are preflighted from the committed manifest before
HTTP dispatch. Unknown arguments, missing requirements, invalid types/ranges,
ambiguous selectors, empty mutations, blocked actions, and missing explicit
session ids fail without executing Unity. Project-defined custom commands pass
through because their contracts are project-specific.

### Command map

| Command | Phase | Purpose |
|---------|-------|---------|
| `cs setup` | pre | Install/version-check the Unity package |
| `cs status` | pre | Package, connection, and version status |
| `cs exec --file FILE` | post | Execute raw C# as a fallback |
| `cs command --input FILE --json` | post | Run one framework command |
| `cs batch --input FILE --json` | post | Run multiple commands in one request |
| `cs health` | post | Check service health |
| `cs refresh [--wait TIMEOUT] [--exit-playmode]` | post | Refresh assets and compile |
| `cs list-commands --offline … --json` | pre/post | Discover committed contracts by domain/tier/id |
| `cs list-commands … --json` | post | Inspect the installed package's live registry |
| `cs catalog sync` / `cs catalog list` | post | Maintain the custom-command catalog |
| `cs snippets …` | post | Browse, run, and maintain reusable snippets |

## Architecture

```text
Agent harness
  └─ skills/unity-cli/SKILL.md (routing and operating rules)
      └─ scripts/cli/cs.py (argparse and handlers)
          └─ core_bridge.py (dynamic csharpconsole_core import)
              └─ HTTP service in Unity Editor/Player
```

The CLI does not bundle `csharpconsole_core`. It resolves it from the installed Unity
package so client and service versions stay aligned:

1. A `file:` dependency in `Packages/manifest.json`.
2. `Library/PackageCache/com.zh1zh1.csharpconsole@*/Editor/ExternalTool~/console-client/`.

`ConsoleSession` wires the core client, command protocol, configuration, output,
response parser, and HTTP transport into the CLI operations. Clearly refused
connections are retried once after one second to tolerate Unity domain reloads.
Timeouts, HTTP errors, and connection resets are not retried because a mutation may
already have executed.

### Version and machine-local state

- The version source is `skills/unity-cli/scripts/cli/VERSION`; keep it in lockstep
  with release tag `vX.Y.Z`.
- `version_check.py` only checks compatibility. `cs.py` pins a plain git package
  source to the newest tag on the CLI's `major.minor` line when setup writes the
  manifest; `file:` sources, explicit fragments, and failed tag queries are left
  unpinned.
- Package-path cache and snippet usage statistics live in a per-project user cache
  (`%LOCALAPPDATA%\unity-cli\<project-key>\` on Windows or
  `$XDG_CACHE_HOME/unity-cli/<project-key>/` elsewhere), never in the project tree.
- The committed custom-command catalog and snippet audit remain project state; see
  the skill references for their exact locations.

## Repository structure

```text
skills/unity-cli/SKILL.md                 Skill entry and routing
skills/unity-cli/references/*.md          Topic-specific operating guidance
skills/unity-cli/scripts/cli/cs.py        CLI dispatcher
skills/unity-cli/scripts/cli/command_index.py
skills/unity-cli/scripts/cli/command_manifest.json
skills/unity-cli/scripts/cli/core_bridge.py
skills/unity-cli/scripts/cli/paths.py     Per-project cache paths
skills/unity-cli/scripts/cli/VERSION      Release version
```

Everything ships in this one skill; there are no slash commands.

## Testing

Run the pure-stdlib unit suite after CLI changes:

```bash
python -B -m unittest discover -s skills/unity-cli/scripts/cli -p "test_*.py" -v
```

There is no build or dependency-install step.

## Release process

For a requested `X.Y.Z` release:

1. Rename the pending `CHANGELOG.md` section to
   `## [X.Y.Z] - YYYY-MM-DD`, then add a fresh `## [Unreleased]` above it.
2. Set `skills/unity-cli/scripts/cli/VERSION` to bare `X.Y.Z`.
3. Commit the release changes.
4. Create local tag `vX.Y.Z`.

`.github/workflows/release.yml` extracts the matching changelog section for release
notes. Never push commits or tags without explicit user confirmation.

## Conventions

- Preserve pure stdlib Python; do not add dependencies or a build step.
- Prefer focused changes and keep agent-facing instructions concise.
- Machine-local state must never be written into the project tree.
- Never use `git push` without explicit user confirmation.
