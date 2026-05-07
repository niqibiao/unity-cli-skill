# Self-Evolving C# Snippet Library Design

## Overview

A self-evolving library of reusable C# snippets executed through the existing Roslyn REPL (`cs exec`), discovered and evolved by the agent through dedicated CLI commands and a `unity-cli-snippets` skill. Snippets live as project-local data; they do not participate in Unity script compilation. The library grows from agent-distilled patterns gated by an automated validation step and tracked by usage frequency.

This is a third tier alongside the existing built-in/custom command sources, deliberately independent of both.

## Constraints (locked in by user)

- **Project-local** storage in `.unity-cli/snippets~/` (committed by project repo). The trailing `~` is defensive: if the directory is ever copied into `Assets/`, Unity skips it.
- **Zero compilation impact**: only `.md` files; no `.cs` files anywhere in the library; everything runs through `cs exec` (Roslyn).
- **Skill is the operator's manual**, not the data. The plugin ships `skills/unity-cli-snippets/SKILL.md` containing rules; snippet bodies live with the project.
- **Validation gate** + **usage frequency tracking** for evolution. No auto-append from exec history.
- **Three independent command tiers**: `cs list-commands` (Unity-side compiled), `cs catalog` (cached subset), `cs snippets` (this proposal). They never read each other.

## Storage Layout

```
<project-root>/
  .unity-cli/
    catalog.json                  # existing — Unity custom command cache
    snippets~/                    # NEW — snippet bodies, one file per snippet
      scene.find_active_in_layer.md
      asset.find_unused_materials.md
      ...
    snippets-stats.json           # NEW — invocation counts, last_used, failure streaks
    snippets-audit.json           # NEW — created_at, verified_at, deprecated, supersedes
```

- `snippets~/<id>.md` is the only thing the agent ever reads (and only via `cs snippets show` or `cs snippets use`).
- `snippets-stats.json` is committed by default (team sees usage heat); user can opt out via `.gitignore`.
- `snippets-audit.json` is committed by default (audit trail).

## Snippet Schema

A snippet `.md` file is **frontmatter + a single `csharp` fenced block defining a method named `Run`**. No placeholders inside the body — types are expressed in `Run`'s signature, and the CLI generates the typed call.

```markdown
---
id: scene.find_active_in_layer
summary: Find active GameObjects in a specific layer
safety: read-only
args:
  - name: layerName
    type: string
    required: true
    description: Layer name (case-sensitive)
example:
  layerName: "Default"
---

```csharp
using System.Linq;
List<string> Run(string layerName) {
    return UnityEngine.Object.FindObjectsOfType<GameObject>()
        .Where(g => g.activeInHierarchy && LayerMask.LayerToName(g.layer) == layerName)
        .Select(g => g.name)
        .ToList();
}
```
```

### Frontmatter fields (all required unless noted)

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | dotted identifier, regex `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, globally unique |
| `summary` | string | one line; this is what `search` indexes against |
| `safety` | enum | `read-only` \| `mutates-undoable` \| `mutates` |
| `args` | list | each entry: `name`, `type`, `required` (default true), `description` (optional) |
| `example` | object | one example arg dict; used by the validation gate. Required even for zero-arg snippets (`{}`) |

Audit and stats fields (`created_at`, `verified_at`, `invocations`, `last_used`, `deprecated`, `supersedes`, etc.) are **not** in the snippet file. They live in `snippets-audit.json` / `snippets-stats.json` so reading a snippet is cheap and writing it doesn't churn git diffs every use.

### Body convention

- Must declare exactly one method named `Run` whose signature matches `args` (in order).
- May include `using` directives, helper types, or helper local functions before `Run`.
- The CLI never edits the body; validation rejects bodies that don't contain a parseable `Run` method.
- Across `cs snippets use` calls each submission redefines `Run`; this is by design (snippets are stateless w.r.t. each other). Implementation may name-mangle on submission to avoid Roslyn shadowing pitfalls — transparent to the snippet author.

## Type Substitution Table

The CLI converts a JSON value (from `--args` or `example`) into a C# literal expression that's spliced into the auto-generated call line. The snippet body never sees the substitution; it just receives a typed C# value.

| `type` | Input shape | C# literal generated |
|--------|-------------|----------------------|
| `string` | `"foo"` | `"foo"` (JSON-escaped) |
| `int` | `42` | `42` |
| `float` | `3.14` | `3.14f` |
| `bool` | `true` / `false` | `true` / `false` |
| `vector2` | `[x, y]` | `new Vector2(x, y)` (each as `float`) |
| `vector3` | `[x, y, z]` | `new Vector3(x, y, z)` |
| `vector4` | `[x, y, z, w]` | `new Vector4(x, y, z, w)` |
| `color` | `[r, g, b]` or `[r, g, b, a]` | `new Color(r, g, b, a)` (alpha defaults to 1) |
| `expr` | string | spliced **verbatim**; escape hatch for arbitrary C# (e.g. `"Camera.main"`). Snippet author owns correctness. |

Quaternion is intentionally absent. Snippets needing rotations either accept a `vector3` and call `Quaternion.Euler(...)` inside `Run`, or accept a `vector4` and `new Quaternion(...)` it. Keeps the type table small and unambiguous.

## CLI Surface

All commands return the standard envelope `{ ok, exitCode, summary, data }` and accept the existing shared flags (`--project`, `--json`, `--ip`, `--port`, ...).

```
cs snippets list   [--tag T] [--safety S] [--include-deprecated] [--sort hot|cold|recent]
cs snippets show   <id>
cs snippets search <query> [--top N]
cs snippets use    <id> [--args '<json>'] [--dry-run]
cs snippets add    <id> --file <md-path> [--no-validate]
cs snippets update <id> [--file <md-path>] [--set key=value]
cs snippets deprecate <id> [--reason "..."] [--supersede <new-id>]
cs snippets remove <id>
cs snippets prune  [--max-age-days N] [--min-uses M] [--remove] [--dry-run]
cs snippets stats  [--id <id>]
```

### Behaviors

- **`use`**: validates `--args` against the snippet's `args` schema; generates `Run(<lit>, <lit>, ...)`; submits `<body>` + `<call>` as one `cs exec` submission. Increments stats on `ok=true`. On failure: increments `failures`, `consecutive_failures`; auto-deprecates after 3 consecutive failures.
- **`add`**: see Validation Gate below. Refuses to overwrite an existing id (use `update`).
- **`update --file`**: re-runs validation gate. `update --set` (metadata-only) skips validation.
- **`deprecate`**: writes audit entry; snippet stays on disk and remains usable but is hidden from default `list` / `search`.
- **`remove`**: hard delete (file + audit + stats entries). Reserved for the rare case where deprecate-then-prune isn't enough.
- **`search`**: lexical match over `id` / `summary`; results are token-cheap (`{id, summary, args-summary}` per hit, no body). Default `--top 5`.
- **`prune`**: default action is to mark deprecated, not delete. `--remove` deletes deprecated entries older than 30 days.

The `--help` text for `cs list-commands` and `cs catalog` cross-references `cs snippets` (and vice versa) so the agent can navigate concept boundaries.

## Validation Gate

Triggered by `add` and by `update --file`. Steps for a snippet with safety class S and one `example`:

1. **Parse**: validate frontmatter shape; `id` regex + uniqueness; `args` schema sanity; example covers all required args.
2. **Substitute**: render the call line `Run(<typed-literal>, ...)` from `example`.
3. **Execute**: branch on safety:
   - `read-only`: POST `<body> + <call>` to `cs exec`; require `ok=true && exitCode=0`.
   - `mutates-undoable`: same, then immediately POST `cs exec "UnityEditor.Undo.PerformUndo();"` and require `ok=true`. Snippet body is responsible for using `Undo.RegisterCompleteObjectUndo` etc; CLI does not check this statically.
   - `mutates`: refused unconditionally. To force, caller passes `--no-validate`; the audit entry is marked `unverified: true` and `cs snippets list` prefixes the row with ⚠.
4. **Persist**: on success, write body to `snippets~/<id>.md`; write audit entry with `created_at` / `verified_at`; initialize stats entry.
5. **Fail-closed**: any failure prints (a) the rendered submission, (b) the `cs exec` response, (c) which step failed. No file is written.

`update --file` follows the same flow; the snippet's existing stats are preserved across the update; audit gets a new `verified_at`.

## Self-Evolution Rules (codified in the skill)

These rules go into `skills/unity-cli-snippets/SKILL.md` as hard guidance:

**Lookup-first (strict)**

> Before writing ad-hoc `cs exec` for any non-trivial Unity automation task, run `cs snippets search <task description>`. "Non-trivial" = >3 lines, or uses LINQ / reflection / AssetDatabase / multi-step. **Never** `Read` or `ls` `.unity-cli/snippets~/` directly.

**Distill criteria** (all must hold):

- Code is parameterized (or trivially can be parameterized into 1–4 typed args).
- Solves a recurring concept: query, batch op, common workflow.
- Either: took >2 attempts to get right, OR the user signaled "save this".

**Anti-criteria** (any disqualifies):

- Project-specific one-shot ("rename this exact path").
- Trivial enough that the snippet wrapper is no shorter than the original.
- Depends on project-private types not in standard Unity.
- Half-working / WIP.

**Collision handling**

- `use <id>` failed → diagnose first.
- Snippet bug → `update`, document Unity version in audit if it's API drift.
- Project-specific issue → don't update; ad-hoc this time.

**Supersede flow**

- New snippet replaces old → `cs snippets deprecate <old> --supersede <new-id>`. Don't `remove` immediately; let `prune` retire it after the cool-down.

## Stats & Aging

`.unity-cli/snippets-stats.json`:

```json
{
  "version": 1,
  "snippets": {
    "scene.find_active_in_layer": {
      "invocations": 12,
      "successes": 11,
      "failures": 1,
      "last_used": "2026-05-06T10:30:00Z",
      "last_failure": "2026-04-28T14:00:00Z",
      "consecutive_failures": 0
    }
  }
}
```

### Default policy

| State | Trigger | Action |
|-------|---------|--------|
| Cold | `last_used > 90d ago && invocations < 3` | mark deprecated |
| Broken | `consecutive_failures >= 3` | auto-deprecate; audit `reason: "3 consecutive failures since <date>"` |
| Hot | `invocations > 10 && last_used within 7d` | highlighted in `list --sort hot` (informational) |

`cs snippets prune`:

- Default: marks cold/broken as deprecated, does not delete.
- `--remove`: deletes deprecated entries whose `deprecated_at > 30d ago`.
- `--dry-run`: prints the plan, takes no action.

## SKILL.md Outline (`skills/unity-cli-snippets/SKILL.md`)

Target ≤ 800 tokens. Fixed size; does not enumerate snippets.

```
---
name: unity-cli-snippets
description: >
  Self-evolving library of reusable C# snippets executed via cs exec. Use when
  performing Unity Editor operations that need custom code: scene queries, batch
  ops, workflow automation. Library lives in project at .unity-cli/snippets~/
  and grows through agent-distilled patterns. Triggers on any non-trivial cs
  exec scenario, recurring Unity automation, or when the user mentions "save as
  snippet" / "reuse this".
---

# Unity CLI Snippets

## Lookup-first (strict)
Always run `cs snippets search <description>` before ad-hoc cs exec.
NEVER ls/Read .unity-cli/snippets~/ directly.

## Workflow
search → (show) → use → (distill if reusable)

## CLI quick reference
[6-line table: list / show / search / use / add / deprecate]

## Snippet anatomy
[one full annotated example showing the Run-method convention]

## When to distill / NOT to distill
[the 4+4 rules from the spec]

## Safety classes
- read-only / mutates-undoable / mutates: one line each

## DO NOT
- Read .unity-cli/snippets~/ directly
- Hand-edit snippet .md files (use add/update)
- Skip search before ad-hoc exec
- Distill project-specific one-offs

## Boundary with cs command / cs exec
- Built-in/custom command available → cs command
- One-off ad-hoc → cs exec
- Reusable ad-hoc → cs snippets
```

## Three-Tier Independence

| Source | Data location | Compiled? | Persistent? |
|--------|--------------|-----------|------------|
| `cs list-commands [--type builtin\|custom]` | Unity in-process registry | Yes (Unity) | No |
| `cs catalog list` | `.unity-cli/catalog.json` | Yes (cached) | Yes |
| `cs snippets list` | `.unity-cli/snippets~/*.md` | No (Roslyn eval) | Yes |

Hard rules:

- `cs list-commands` does **not** show snippets.
- `cs catalog sync` does **not** touch snippets.
- `cs snippets` does **not** read catalog or list-commands.
- `--help` cross-references between the three so the agent can navigate.

Skill-level updates to existing skills (small additions, not rewrites):

- `unity-cli-command/SKILL.md`: add a line "if no built-in/custom command matches → check `unity-cli-snippets` before falling back to `unity-cli-exec-code`".
- `unity-cli-exec-code/SKILL.md`: add a line "before writing ad-hoc, run `cs snippets search`. After solving a non-trivial reusable task, consider distilling."

## Out of Scope (Explicit Non-Goals)

- **No auto-distill from `cs exec` history.** csharpconsole doesn't track exec history; even if it did, project-specific code would pollute the library. Distillation is always an explicit `cs snippets add <id> --file <md>` action by the agent.
- **No snippet-calling-snippet.** Composition via shared REPL state is fragile across domain reloads. If you need composition, the agent sequences multiple `cs snippets use` calls and threads state through `cs exec` between them.
- **No `Quaternion` type.** Snippets that need rotations accept `vector3` (Euler) or `vector4` (raw) and construct inside `Run`.
- **No embedding-based search.** Lexical match only; stdlib-only constraint.
- **No PR-back-to-plugin tooling.** Sharing snippets across projects is left as a manual copy-paste, deliberately. The plugin's `unity-cli-snippets` skill ships **no** baseline snippets — every project's library starts empty.
- **No snippet-renaming.** `id` is the primary key; renaming = deprecate-then-add-new with `--supersede`.

## Deliverables

| # | Change | Files |
|---|--------|-------|
| 1 | Type substitution + call generation | `cli/snippets/render.py` (new) |
| 2 | Snippet file IO + frontmatter parsing | `cli/snippets/store.py` (new) |
| 3 | Validation gate | `cli/snippets/validate.py` (new) |
| 4 | Stats / audit IO + aging policy | `cli/snippets/stats.py` (new) |
| 5 | `cs snippets` subcommand wiring | `cli/cs.py` |
| 6 | Skill content | `skills/unity-cli-snippets/SKILL.md` (new) |
| 7 | Cross-reference updates in existing skills | `skills/unity-cli-command/SKILL.md`, `skills/unity-cli-exec-code/SKILL.md` |
| 8 | `.gitignore` adjustments (no-op by default; document opt-out) | `.gitignore`, README |
| 9 | CLAUDE.md + READMEs | `CLAUDE.md`, `README.md`, `README_zh.md` |
| 10 | CHANGELOG entry under `[Unreleased]` | `CHANGELOG.md` |

## Execution Timeline

| Phase | Deliverables |
|-------|--------------|
| D0 | Render + Store + Validate + Stats modules with unit-style smoke tests via `cs exec` |
| D1 | `cs snippets` CLI surface (list / show / search / use / add / update / deprecate / remove / prune / stats) |
| D2 | `unity-cli-snippets` SKILL.md, cross-references in existing skills, docs (CLAUDE.md / READMEs / CHANGELOG) |

Each phase is independently reviewable and shippable; D1 depends on D0; D2 depends on D1.
