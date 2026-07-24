---
name: unity-cli
description: >
  Drive the Unity Editor/Player from the command line via the C# Console service.
  Use for ANY Unity Editor automation: GameObject / component / transform / scene /
  prefab / material / asset / screenshot / profiler commands; executing raw C# in the
  live Editor; refreshing / recompiling; querying connection & editor status; managing
  a reusable C# snippet library; and syncing the custom-command catalog. Triggers on
  Unity editor tasks, "run C#" / "exec" / "eval" in Unity, create / modify / find
  GameObjects, screenshots, play mode, profiling, or "set up / 安装 unity-cli".
---

# Unity CLI

One CLI (`cs`) drives everything; subcommands cover all operations.
Decision order for any task: **built-in command → snippet → raw exec**.

## Running `cs`

`cs` below = `python "<SKILL_DIR>/scripts/cli/cs.py"`, where `<SKILL_DIR>` is THIS
skill's base directory (shown when the skill loads — an absolute path). Expand `cs`
to that full command on every call. **Do not pass
`--project`** — the CLI auto-detects the Unity project (it walks up from the working
directory, and from its own committed location). Prefix with
`PYTHONDONTWRITEBYTECODE=1` so running the CLI leaves no `__pycache__` in the project:

```bash
PYTHONDONTWRITEBYTECODE=1 python "<SKILL_DIR>/scripts/cli/cs.py" command --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req.json"
```

No bootstrap/copy step — the CLI runs in place from this skill. First-time use needs the
**Unity package** in the project: `cs setup` installs it (see `references/setup.md`), and
`cs status` reports `NOT FOUND` until Unity resolves it. The first command auto-caches the
resolved package path (machine-local, under your home cache), so an explicit `cs setup` is
a convenience, not a gate. **`cs setup` writes the project's `Packages/manifest.json` — a
shared project file. Never run it (or `--update`) unprompted: tell the user what it will
write and get their go-ahead first.**

### Passing parameters — C# via `--file`, JSON via `--input` (never inline)

Never pass C# code or params as inline shell arguments — write a file with your file
tool, then hand the CLI the path. Two channels:

- **C# code → `--file <path>.cs`** (raw C#, zero escaping). Always use this for
  `exec` — never wrap code in a JSON `{"code": …}` payload, where every quote /
  backslash / newline must be JSON-escaped (a raw `.cs` file needs none).
- **Structured params → `--input <file>` JSON** (or `-` for stdin) for `command` /
  `batch`.

**Scratch file location (mandatory):** the absolute path
`<project-root>/Temp/CSharpConsole/AgentScratch/` — inside the Unity project's own
`Temp/` (Unity-managed, never imported, normally git-ignored; the C# Console service
already keeps its state under `Temp/CSharpConsole/`). Always write the **full
absolute path** — your file tool resolves relative paths against its own cwd, not
the project. Name files by semantic task (`inspect-camera.cs`,
`req-create-cube.json`): overwrite the same file when revising the same task
serially; add a one-shot random suffix only when another agent is known to work the
same project concurrently. The directory lives and dies with the Unity Editor
(closing it may delete `Temp/`) — scratch files are one-shot execution payloads,
never durable storage; anything worth reusing across conversations goes into the
snippet library. Clean only `AgentScratch/`; never delete `Temp/CSharpConsole/`
itself (the service's `refresh_state.json` lives there). **Never write scratch
files under `Assets/`**: typical REPL snippets are not valid standalone project
sources — the import very likely fails project compilation, blocking
refresh/domain-reload workflows, and after an editor restart the console service
may not start at all.

```bash
cs exec    --file  "<project-root>/Temp/CSharpConsole/AgentScratch/inspect-camera.cs"
cs command --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req-create-cube.json"  # {"ns":"gameobject","action":"create","args":{"name":"Cube"}}
cs batch   --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req-batch.json"        # {"commands":[ … ],"stopOnError":true}
```

### REPL context — share only when needed

`cs exec` starts a fresh REPL session by default. Keep that default for self-contained
one-shot code. When a later submission intentionally depends on variables, `using`s,
types, or helpers created by an earlier submission, generate one opaque session id
for that task and pass the exact same `--session <id>` on every dependent call:

```bash
cs exec --session agent-a1b2c3 --file "<project-root>/Temp/CSharpConsole/AgentScratch/inspect-camera.cs"
cs exec --session agent-a1b2c3 --file "<project-root>/Temp/CSharpConsole/AgentScratch/use-camera.cs"
```

Do not attach a shared session to unrelated work. Different agents/tasks must use
different ids. Named session state is ephemeral: a domain reload clears it, so after
`cs refresh` either rebuild the context with self-contained code or start a new id.
Use a new id whenever a clean context is simpler than resetting the old one. See
`references/exec-code.md` for reset and lifecycle details.

## Routing — pick the subcommand

| Task | Subcommand | Detail |
|------|------------|--------|
| Structured editor ops (GameObject/component/scene/prefab/asset/material/screenshot/profiler) | `cs command --input` | references/commands.md |
| Raw C# in the live Editor (fallback) | `cs exec` | references/exec-code.md |
| Reusable C# snippet library | `cs snippets …` | references/snippets.md |
| Audit / validate snippets | `cs snippets doctor` / `stats` | references/snippets-audit.md |
| Refresh / recompile after writing .cs | `cs refresh` | references/refresh.md |
| Sync custom-command catalog / maintainer audit | `cs catalog sync` / `cs list-commands` | references/catalog.md |
| Connection / editor status | `cs status` / `cs health` | references/status.md |
| First-time package setup / version-check | `cs setup` | references/setup.md |

## Conventions (all subcommands)

- `--json` only where the payload comes back as structured data: **`command`,
  `list-commands`, `batch`** — envelope
  `{ "ok", "exitCode", "summary", "data" }`, check `ok` / `exitCode`. Every other
  subcommand (`exec`, `status`, `refresh`, `health`, `setup`, `catalog`,
  `snippets`) prints an equivalent, cheaper text form — omit `--json`; success =
  exit code 0, errors go to stderr. Add `--json` to `exec` only when you need the
  structured result envelope instead of the REPL's text output.
- **Never pass `--project`** — the CLI auto-detects the project. Pass `--project <path>`
  only to deliberately target a different project.
- Prefer `cs command` over `cs exec` when a built-in covers the task; check the snippet
  library (`cs snippets search <desc>`) before falling back to ad-hoc `cs exec`.
- A `⚠ version mismatch` warning means the installed Unity package and the CLI are on
  different `major.minor` lines — align them (see references/setup.md). It warns; it
  does not block.
- `--json` and the expanded `python … cs.py …` command line are **agent-internal** —
  for you to run and parse, never to paste to the user. When a step needs the user to
  act (e.g. open Unity to resolve the package) and then re-verify, ask them in plain
  language to **check unity-cli status**; run `cs status` yourself to read the result.
