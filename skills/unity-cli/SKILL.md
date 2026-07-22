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
PYTHONDONTWRITEBYTECODE=1 python "<SKILL_DIR>/scripts/cli/cs.py" command --json --input req.json
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
  `batch` (and `complete`, whose `{"code","cursor"}` has no file form).

Write these scratch files **outside the Unity project's `Assets/`** (scratchpad or
temp dir) — anything under `Assets/` triggers a Unity import.

```bash
cs exec    --file snippet.cs          # raw C# — the way to pass code
cs command --json --input req.json    # req.json: {"ns":"gameobject","action":"create","args":{"name":"Cube"}}
cs batch   --json --input req.json    # req.json: {"commands":[ … ],"stopOnError":true}
```

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
  `list-commands`, `batch`, `complete`** — envelope
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
