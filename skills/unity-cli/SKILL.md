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
to that full command on every call, and always pass `--json`. **Do not pass
`--project`** — the CLI auto-detects the Unity project (it walks up from the working
directory, and from its own committed location). Prefix with
`PYTHONDONTWRITEBYTECODE=1` so running the CLI leaves no `__pycache__` in the project:

```bash
PYTHONDONTWRITEBYTECODE=1 python "<SKILL_DIR>/scripts/cli/cs.py" command --json <ns> <action> ['<args-json>']
```

No bootstrap/copy step — the CLI runs in place from this skill. First-time use only
needs the **Unity package** installed into the project (see `references/setup.md`);
`cs status` reports `NOT FOUND` until then. The first command auto-caches the
resolved package path (machine-local, under your home cache), so an explicit
`cs setup` is optional, not a gate.

## Routing — pick the subcommand

| Task | Subcommand | Detail |
|------|------------|--------|
| Structured editor ops (GameObject/component/scene/prefab/asset/material/screenshot/profiler) | `cs command <ns> <action>` | references/commands.md |
| Raw C# in the live Editor (fallback) | `cs exec` | references/exec-code.md |
| Reusable C# snippet library | `cs snippets …` | references/snippets.md |
| Audit / validate snippets | `cs snippets doctor` / `stats` | references/snippets-audit.md |
| Refresh / recompile after writing .cs | `cs refresh` | references/refresh.md |
| Sync custom-command catalog / maintainer audit | `cs catalog sync` / `cs list-commands` | references/catalog.md |
| Connection / editor status | `cs status` / `cs health` | references/status.md |
| First-time package setup / version-check | `cs setup` | references/setup.md |

## Conventions (all subcommands)

- Always `--json`; the envelope is `{ "ok", "exitCode", "summary", "data" }` — check
  `ok` / `exitCode` for success.
- **Never pass `--project`** — the CLI auto-detects the project. Pass `--project <path>`
  only to deliberately target a different project.
- Prefer `cs command` over `cs exec` when a built-in covers the task; check the snippet
  library (`cs snippets search <desc>`) before falling back to ad-hoc `cs exec`.
- A `⚠ version mismatch` warning means the installed Unity package and the CLI are on
  different `major.minor` lines — align them (see references/setup.md). It warns; it
  does not block.
