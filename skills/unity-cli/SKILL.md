---
name: unity-cli
description: >
  Drive a live Unity Editor or Player with unity-cli. Use when a task must inspect
  or change Unity scenes, GameObjects, components, transforms, prefabs, materials,
  project assets, play mode, screenshots, profiler recording, or execute C# inside
  Unity; also use for Unity console maintenance and unity-cli setup, status,
  readiness diagnosis, uncertain-operation recovery, refresh, snippets, or
  custom commands. Do not use for source-only Unity coding that does not require
  interaction with the live Editor or Player.
---

# Unity CLI

One CLI (`cs`) drives the Unity Editor/Player. Built-in commands are grouped into
seven agent-facing domains so only the relevant schema enters context.

## Running `cs`

`cs` below means:

```bash
python "<SKILL_DIR>/scripts/cli/cs.py"
```

`<SKILL_DIR>` is this skill's absolute base directory, supplied when the skill
loads. Expand `cs` to that absolute command on every call and run it without
changing directory.

Do not pass `--project` during normal use. The CLI locates the Unity project by
walking up from both the working directory and its own committed location.
`--project <path>` is only for deliberately targeting a different project.

The CLI runs in place; there is no bootstrap or copy step. `cs setup` is a
convenience, not a gate. If the package is absent or `--update` is used, setup
writes the shared `Packages/manifest.json`. Before any write-producing setup,
state the exact source it will add and obtain the user's approval. See
`references/setup.md`.

## Passing inputs

Never pass C# code or structured command parameters inline through the shell.
Write a file, then pass its absolute path:

- Raw C# for `exec` goes in a `.cs` file via `--file`.
- Structured parameters for `command` and `batch` go in a JSON file via
  `--input`.

The mandatory scratch directory is:

```text
<project-root>/Temp/CSharpConsole/AgentScratch/
```

This is inside Unity's managed `Temp/`, is not imported, and is normally ignored
by git. Always use the full absolute path. Name files for the semantic task and
overwrite the same file when revising it serially; use a random suffix only when
another agent is known to work on the same Unity project concurrently.

Scratch payloads are one-shot. Put reusable C# in the snippet library. Clean only
`AgentScratch/`; never delete the parent `Temp/CSharpConsole/`, which older package
versions may still use for compatibility state.
Never write REPL payloads under `Assets/`: importing them can break project
compilation and prevent the service from restarting.

```bash
cs exec --file "<project-root>/Temp/CSharpConsole/AgentScratch/inspect-camera.cs"
cs command --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req-create-cube.json"
cs batch --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req-batch.json"
```

## REPL sessions

`cs exec` starts a fresh session by default. Keep that default for self-contained
code. When later submissions intentionally depend on variables, `using`s, types,
or helpers created earlier, generate one opaque task-specific id and reuse it:

```bash
cs exec --session agent-a1b2c3 --file "<project-root>/Temp/CSharpConsole/AgentScratch/inspect-camera.cs"
cs exec --session agent-a1b2c3 --file "<project-root>/Temp/CSharpConsole/AgentScratch/use-camera.cs"
```

Never share a session between unrelated tasks or agents. A domain reload clears
session state, so after `cs refresh` rebuild the context or use a new id. See
`references/exec-code.md` for lifecycle and reset details.

## Routing

Use this canonical order for every task: **built-in command → cached custom command →
snippet → raw exec**. Do not skip a matching built-in in favor of C#.

For a structured Unity operation:

1. Choose one domain from the index below.
2. Read `references/commands.md`.
3. Discover only that domain's committed core schema with
   `cs list-commands --offline --domain <domain> --tier core --json`. Offline
   discovery needs neither a Unity project nor a running service.
4. Query `advanced` only when the requested operation is specialized,
   destructive, or absent from core. Keep `--offline`; use
   `--id <namespace/action>` when checking one exact contract. Remove
   `--offline` only when verifying what the currently installed package actually
   registers.
5. Write the selected `ns`, `action`, and `args` to the mandatory scratch JSON
   file, then run `cs command --json --input <file>`.

Do not load or print the unfiltered 60-command registry during routine work.

| Domain | Positive intent | Exclude / route elsewhere |
|---|---|---|
| `editor` | Editor state, play mode, console maintenance | Scene contents, object selection, asset files |
| `scene` | Scene listing/open/save and hierarchy | GameObject properties, prefab contents |
| `objects` | GameObjects, object selection, components, transforms | Project asset movement, prefab-file editing |
| `assets` | Asset search/import/CRUD, materials, and material assignment | Scene hierarchy, prefab contents |
| `prefabs` | Create/instantiate/unpack or directly edit prefab contents | Generic asset paths unrelated to prefab contents |
| `capture` | Scene/Game screenshots and Profiler recording | Structured state inspection |
| `control` | REPL sessions and command discovery | Normal Unity authoring |

### Other subcommands

| Task | Subcommand | Detail |
|---|---|---|
| Raw C# fallback | `cs exec --file` | `references/exec-code.md` |
| Reusable C# | `cs snippets …` | `references/snippets.md` |
| Snippet audit | `cs snippets doctor` / `stats` | `references/snippets-audit.md` |
| Refresh and compile | `cs refresh` | `references/refresh.md` |
| Custom-command catalog | `cs catalog sync` / `list` | `references/catalog.md` |
| Diagnose readiness / uncertain operation | `cs doctor` / `cs doctor --operation UUID` | `references/status.md` |
| Wait without Unity/project mutation | `cs wait-ready --timeout N` (bind refresh op/generation when resuming) | `references/status.md` |
| Package / raw service state | `cs status` / `cs health` | `references/status.md` |
| Package setup | `cs setup` | `references/setup.md` |

## Output conventions

- Use `--json` on `command`, `list-commands`, and `batch`. Their envelope is
  `{ "ok", "exitCode", "summary", "data" }`; check `ok` and `exitCode`.
- Other subcommands print a cheaper text form. Add `--json` to `exec` only when
  its structured envelope is specifically needed.
- A version-mismatch warning means the installed package and CLI use different
  `major.minor` lines. Follow `references/setup.md`; the warning does not itself
  block execution.
- `outcome_unknown` or `operation_in_progress` (exit 4) means the operation is
  unresolved and the CLI deliberately did not create a second dispatch. Run
  `cs doctor --operation <UUID> --json` and verify the affected state; never
  replace that UUID with a new one while it is unresolved.
- Once the local outbox records an operation id as sent, later CLI runs diagnose
  it instead of dispatching it again, even after server retention expires. A new
  UUID represents a new intent, not a retry.
- Expanded CLI commands and JSON payloads are agent-internal. When Unity must be
  opened or focused, tell the user what action is needed in plain language, then
  run `cs status` yourself to verify the result.
