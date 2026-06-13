# Self-Evolving C# Snippet Library Design

## Overview

A self-evolving library of reusable C# snippets executed through the existing Roslyn REPL (`cs exec`), discovered and evolved by the agent through dedicated CLI commands and a `unity-cli-snippets` skill. Snippets live as project-local data; they do not participate in Unity script compilation. The library grows from agent-distilled patterns gated by an automated validation step (smoke test) and tracked by usage frequency.

This is a third tier alongside the existing built-in/custom command sources, deliberately independent of both.

## Constraints (locked in by user)

- **Project-local** storage in `.unity-cli/snippets~/` (committed by project repo). The trailing `~` is defensive: if the directory is ever copied into `Assets/`, Unity skips it.
- **Zero compilation impact**: only `.md` files; no `.cs` files anywhere in the library; everything runs through `cs exec` (Roslyn).
- **Skill is the operator's manual**, not the data. The plugin ships `skills/unity-cli-snippets/SKILL.md` containing rules; snippet bodies live with the project.
- **Validation gate** (smoke test only — *not* a correctness oracle) + **usage frequency tracking** for evolution. No auto-append from exec history.
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
    snippets-audit.json           # NEW — created/verified/deprecated audit trail (committed)
    snippets-stats.json           # NEW — invocation counts, last_used, failure streaks (gitignored by default)
```

- `snippets~/<id>.md` is the only thing the agent ever reads (and only via `cs snippets show` or `cs snippets use`).
- `snippets-audit.json` is committed by default — audit trail is project state.
- `snippets-stats.json` is **gitignored by default** — usage stats are observability data, mutate on every `use`, would create constant PR noise and merge conflicts. The plugin's `cs setup` adds it to project `.gitignore` automatically. Teams that want shared visibility can opt back in.

## Snippet Schema

A snippet `.md` file is **frontmatter + a single `csharp` fenced block** containing top-level `using` directives and a `static` method named `Run`. Authors write plain C# with typed parameters; the CLI handles wrapping and call generation on submission.

```markdown
---
id: scene.find_active_in_layer
summary: Find active GameObjects in a specific layer
safety: read-only
args:
  - name: layerName
    type: string
    description: Layer name (case-sensitive)
example:
  layerName: "Default"
# optional: expected: "<string>" — see Validation Gate (only meaningful when Run returns a string)
---

```csharp
using System.Linq;

static List<string> Run(string layerName) {
    return UnityEngine.Object.FindObjectsOfType<GameObject>()
        .Where(g => g.activeInHierarchy && LayerMask.LayerToName(g.layer) == layerName)
        .Select(g => g.name)
        .ToList();
}
```
```

### Frontmatter fields

| Field | Required | Notes |
|-------|----------|-------|
| `id` | yes | dotted identifier, regex `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`, globally unique |
| `summary` | yes | one line; this is what `search` indexes against |
| `safety` | yes | `read-only` \| `mutates` (see Safety Classes) |
| `args` | yes | list of `{name, type, description?, default?}`; an arg is optional iff it has a `default` |
| `example` | yes | one example arg dict; covers all args without defaults; used by validation gate |
| `expected` | no | opt-in **string**: validation also compares the textual REPL result of `Run(example)` against this value. The exec service `ToString()`s the return value into `data.text` and never emits structured JSON, so deep-equality against JSON values is impossible by construction; snippets wanting structured assertions return a formatted string from `Run`. |

Audit and stats fields (`created_at`, `verified_at`, `invocations`, `successes`, `failures`, `last_used`, `deprecated`, `superseded_by`) are **not** in the snippet file — they live in `snippets-audit.json` / `snippets-stats.json` so reading a snippet is cheap and writing it doesn't churn git diffs every use.

### Body convention

- A single `csharp` fenced block at the top level of the file.
- May contain `using` directives at the top.
- Must declare exactly one method named `Run` whose signature matches `args` in order. `Run` and any helper types/methods must be `static`. Local functions are not allowed (use `static` helper methods at the same level instead).
- May contain additional `static` helper methods or nested types alongside `Run`.
- The CLI never edits the file. Validation rejects bodies that don't contain a parseable `static Run` method.

### Submission isolation (transparent to authors)

`cs snippets use` does not submit the body verbatim. The CLI wraps it for isolation:

```csharp
// extracted from body
using System.Linq;

// auto-generated wrapper
static class __Snip_a1b2c3d4e5f60718 {
    static List<string> Run(string layerName) { /* body */ }
    /* helpers, if any */
}
__Snip_a1b2c3d4e5f60718.Run("Default")
```

The hash in `__Snip_<hash>` is derived from the snippet `id` + body content. This guarantees:

- Repeated `use` of the same snippet reuses the same name (idempotent in the REPL session).
- Different snippets (or modified versions) get different names; no shadowing or symbol collisions.
- Helper types/methods are scoped to the wrapper class, can't leak to subsequent submissions.

## Type Substitution Table

The CLI converts a JSON value (from `--args` or `example`) into a C# literal expression spliced into the auto-generated call line. The snippet body never sees the substitution; it just receives a typed C# value. Generated type names are **always fully qualified** so the call doesn't depend on whatever `using` directives the snippet body chose.

| `type` | Input shape | C# literal generated |
|--------|-------------|----------------------|
| `string` | `"foo"` | `"foo"` (JSON-escaped) |
| `int` | `42` | `42` |
| `float` | `3.14` | `3.14f` |
| `bool` | `true` / `false` | `true` / `false` |
| `vector2` | `[x, y]` | `new UnityEngine.Vector2(x, y)` |
| `vector3` | `[x, y, z]` | `new UnityEngine.Vector3(x, y, z)` |
| `vector4` | `[x, y, z, w]` | `new UnityEngine.Vector4(x, y, z, w)` |
| `color` | `[r, g, b]` or `[r, g, b, a]` | `new UnityEngine.Color(r, g, b, a)` (alpha defaults to 1) |
| `string[]` | `["a", "b"]` | `new string[] { "a", "b" }` |
| `int[]` | `[1, 2, 3]` | `new int[] { 1, 2, 3 }` |
| `float[]` | `[1.0, 2.5]` | `new float[] { 1.0f, 2.5f }` |

No `Quaternion` (snippets that need rotations accept `vector3` Euler or `vector4` raw and construct inside `Run`). No nested arrays. No `expr` (raw verbatim splice) — it would bypass the type system, undermine `safety` semantics, and effectively allow arbitrary code injection at the call site. If a snippet needs an expression-shaped argument, write it inside `Run` rather than parameterizing it.

### Optional args and defaults

An arg with a `default:` field is optional. Call generation:

- If `--args` includes a value → use it.
- If omitted → use `default`.
- An optional arg's `default` value is rendered with the same type substitution rules.

`example` must cover all required args (no `default`). Optional args may be omitted from `example` (then default is used during validation).

## CLI Surface

All commands return the standard envelope `{ ok, exitCode, summary, data }` and accept the existing shared flags (`--project`, `--json`, `--ip`, `--port`, ...).

```
cs snippets list   [--safety S] [--include-deprecated] [--sort hot|cold|recent]
cs snippets show   <id>
cs snippets search <query> [--top N]
cs snippets use    <id> [--args '<json>'] [--dry-run]
cs snippets add    <id> --file <md-path> [--no-validate]
cs snippets update <id> [--file <md-path>] [--set key=value]
cs snippets deprecate <id> [--reason "..."] [--supersede <new-id>]
cs snippets prune  [--cold] [--max-age-days N] [--min-uses M] [--remove] [--dry-run]
cs snippets stats  [--id <id>]
cs snippets doctor [--revalidate]
```

(10 subcommands. No standalone `remove` — deletion goes through `deprecate` then `prune --remove` to keep destructive paths funneled through one policy.)

### Behaviors

- **`use`**: validates `--args` against the snippet's schema; generates `__Snip_<hash>.Run(<lit>, ...)`; submits wrapped body + call as one `cs exec` submission. On `ok=true`: `successes++`, `last_used = now`, `consecutive_failures = 0`. On Run-body error (compilation or runtime exception during `Run`): `failures++`, `consecutive_failures++`. Caller errors (bad `--args`, snippet not found) and environment errors (Unity not running, network) do **not** count toward failures.
- **`add`**: validation gate, see below. Refuses to overwrite an existing id (use `update`).
- **`update --file`**: re-runs validation gate. Existing stats preserved; audit gets a new `verified_at`.
- **`update --set key=value`**: restricted to fields that cannot affect execution: `summary`, an arg's `description`. Changing `args`, `example`, `safety`, `expected`, or anything in the code body requires `--file` (full re-validation).
- **`deprecate`**: writes audit entry; snippet stays on disk and remains usable but is hidden from default `list` / `search`.
- **`search`**: lexical match over `id` / `summary`; results are token-cheap (`{id, summary, args-summary}` per hit, no body). Default `--top 5`.
- **`prune`**: default action targets only already-deprecated entries (does nothing to live ones). With `--cold`, also marks cold snippets (see Aging) as deprecated. `--remove` deletes deprecated entries whose `deprecated_at > 30d ago`. `--dry-run` prints the plan, takes no action.
- **`doctor`**: anti-rot health check, read-only by default. Reports integrity drift (orphan files without audit entries, audit entries without files, corrupt / id-mismatched files), staleness (broken / cold live snippets, unverified mutates), and removal candidates (deprecated past cool-down). With `--revalidate` (requires running Unity, gated by an upfront health check), re-runs the validation gate on every live read-only snippet — the API-drift detector — refreshing `verified_at` on passes and reporting `revalidation_failed` findings otherwise. Doctor never touches usage stats: diagnostics are not invocations. The `unity-cli-snippets-audit` skill is its operator manual (triage table, destructive-action confirmation rules).

The `--help` text for `cs list-commands` and `cs catalog` cross-references `cs snippets` (and vice versa) so the agent can navigate concept boundaries.

## Safety Classes

Two classes — collapsed from an earlier three-class design after recognizing that `Undo.PerformUndo()` is not a sufficient validation oracle (a snippet that forgot to register undo passes the gate while leaving editor state mutated, and undo groups can affect prior unrelated editor actions).

- **`read-only`**: pure query, no scene/asset/file/setting changes. Auto-validated by the gate.
- **`mutates`**: any side effect on scene, assets, files, ProjectSettings, etc. **Cannot** be auto-validated. `add` / `update --file` requires `--no-validate`; the audit entry is marked `unverified: true`; `cs snippets list` prefixes the row with ⚠.

Snippets that touch `AssetDatabase`, write files, change ProjectSettings, trigger refreshes, or affect domain reload state are always `mutates`. The classification is the snippet author's responsibility; the CLI does not infer it.

## Validation Gate

Triggered by `add` and by `update --file`. Steps for a snippet with safety class S:

1. **Parse**: validate frontmatter shape; `id` regex + uniqueness; `args` schema sanity; `example` covers all required args; body has parseable `static Run` matching the args.
2. **Render**: generate the wrapped submission (class wrapper + call line) using `example` values.
3. **Execute** (only if `S == read-only`):
   - POST `cs exec <wrapped-submission>`.
   - Require `ok=true && exitCode=0`.
   - If `expected` is present: compare the textual result (`data.text`, the `ToString()` of Run's return value) against the `expected` string. Mismatch → fail.
4. **Refuse** (if `S == mutates`): require `--no-validate`. With it, write the file but mark `unverified: true` in audit and skip steps 3.
5. **Persist**: on success, write body to `snippets~/<id>.md`; write audit entry with `created_at` / `verified_at` / `unverified` flag; initialize stats entry (zeros, `last_used = created_at`).
6. **Fail-closed**: any failure prints (a) the rendered submission, (b) the `cs exec` response, (c) which step failed. No file is written.

The gate is a **smoke test**, not a correctness oracle. Authors who want stronger validation use the `expected` field; without it, validation only proves the snippet runs without crashing under the example inputs.

## Self-Evolution Rules (codified in the skill)

These rules go into `skills/unity-cli-snippets/SKILL.md` as hard guidance:

**Decision order (strict)**

> Before writing ad-hoc `cs exec` for any non-trivial Unity automation task:
> 1. `cs list-commands [--type custom]` — is there a built-in or custom command? Use `cs command` if so.
> 2. `cs snippets search <description>` — is there a matching snippet? Use it.
> 3. Only if neither match, write ad-hoc `cs exec`.
>
> "Non-trivial" = >3 lines, or uses LINQ / reflection / AssetDatabase / multi-step.
> **Never** `Read` or `ls` `.unity-cli/snippets~/` directly.

**Distill criteria** (all must hold):

- Code is parameterized (or trivially can be parameterized into 1–4 typed args).
- Solves a recurring concept: query, batch op, common workflow.
- Either: user signaled "save this", OR you judge the pattern likely to recur.

**Anti-criteria** (any disqualifies):

- One-shot operation tied to an exact path/name/id.
- Trivial enough that the snippet wrapper is no shorter than the original.
- Depends on ephemeral or generated symbols (autogen code, runtime-injected types) that won't exist if the project is reopened from a fresh checkout.
- Half-working / WIP.

**Collision handling**

- `use <id>` failed → diagnose first.
- Snippet bug → `update --file`, document Unity version in audit if it's API drift.
- Project-specific issue → don't update; ad-hoc this time.

**Supersede flow**

- New snippet replaces old → `cs snippets deprecate <old> --supersede <new-id>`. Don't `prune --remove` immediately; let the 30-day cool-down handle it.

## Stats & Aging

`.unity-cli/snippets-stats.json` (gitignored by default):

```json
{
  "version": 1,
  "snippets": {
    "scene.find_active_in_layer": {
      "successes": 11,
      "failures": 1,
      "last_used": "2026-05-06T10:30:00Z",
      "last_failure": "2026-04-28T14:00:00Z",
      "first_failure_in_streak": null,
      "consecutive_failures": 0
    }
  }
}
```

`invocations` is derived as `successes + failures` whenever needed (not stored). `hot` / `cold` heat metrics are based on `successes`, not the derived total — failed runs don't increase heat.

For a snippet that has never been used, `last_used` is treated as equal to its `created_at` from the audit file (so `cold` rules don't immediately fire on freshly-added snippets).

### Default policy

| State | Trigger | Default action |
|-------|---------|----------------|
| Cold | `last_used > 90d ago && successes < 3` | **informational only** — highlighted in `list --sort cold`; no auto-action |
| Broken | `consecutive_failures >= 5 && (last_failure − first_failure_in_streak) >= 7d` | auto-deprecate **at `use` time** when the qualifying Run-body failure is recorded (never from `prune`); audit `reason: "5 consecutive failures over <span>d since <date>"` |
| Hot | `successes > 10 && last_used within 7d` | highlighted in `list --sort hot` (informational) |

Auto-deprecation on the broken rule requires both **count** (5 strikes) and **time spread** (≥ 7 days between the streak's first and last failure). Transient flakes during a single bad-Unity-state session do not trip it.

`cs snippets prune`:

- **Default**: only acts on already-deprecated entries; `--remove` deletes those with `deprecated_at > 30d ago`. Without `--remove`, it's a no-op.
- **`--cold`**: opt-in cold pruning — marks cold snippets as deprecated (does not delete). For users who want to actively curate; never automatic.
- **`--dry-run`**: prints the plan, takes no action.

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

## Decision order (strict)
1. cs list-commands [--type custom]    — built-in / custom command?
2. cs snippets search <description>    — matching snippet?
3. Ad-hoc cs exec only if neither.
NEVER ls/Read .unity-cli/snippets~/ directly.

## Workflow
search → (show) → use → (distill if reusable)

## CLI quick reference (the 5 you'll actually use)
search / show / use / add / deprecate
[full set: list / show / search / use / add / update / deprecate / prune / stats — see cs snippets --help]

## Snippet anatomy
[one full annotated example showing the static Run convention]

## When to distill / NOT to distill
[the criteria + anti-criteria from the spec]

## Safety classes
- read-only: pure query, auto-validated
- mutates: side effects, requires --no-validate, marked unverified

## Validation gate
Smoke test only. Use the optional `expected` field for return-value assertions.

## DO NOT
- Read .unity-cli/snippets~/ directly
- Hand-edit snippet .md files (use add/update)
- Skip search before ad-hoc exec
- Distill one-shot operations or trivial one-liners
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
- `unity-cli-exec-code/SKILL.md`: add a line "before writing ad-hoc, run `cs list-commands` and `cs snippets search`. After solving a non-trivial reusable task, consider distilling."

Operational independence (the agent actually following decision order) is enforced by skill wording, not by CLI invariants — this is by design; the CLI itself stays minimal and unopinionated.

## Out of Scope (Explicit Non-Goals)

- **No `mutates-undoable` safety class.** Originally proposed; cut after recognizing `Undo.PerformUndo()` is not a sufficient rollback oracle. Authors classify mutating snippets as `mutates` and skip auto-validation.
- **No `expr` arg type.** Splicing arbitrary C# at the call site bypasses type checks and undermines safety classification. Snippets needing expression-shaped values build them inside `Run`.
- **No `Quaternion` arg type.** `vector3` (Euler) or `vector4` (raw) accepted, snippet constructs inside `Run`.
- **No nested arrays / dictionaries / object args.** Keeps the type table small. JSON object args are out of scope.
- **No auto-distill from `cs exec` history.** csharpconsole doesn't track exec history; even if it did, project-specific code would pollute the library. Distillation is always an explicit `cs snippets add <id> --file <md>` action by the agent.
- **No snippet-calling-snippet.** Composition via shared REPL state is fragile across domain reloads. If you need composition, the agent sequences multiple `cs snippets use` calls.
- **No embedding-based search.** Lexical match only; stdlib-only constraint.
- **No PR-back-to-plugin tooling.** Sharing snippets across projects is left as a manual copy-paste, deliberately. The plugin's `unity-cli-snippets` skill ships **no** baseline snippets — every project's library starts empty.
- **No snippet-renaming.** `id` is the primary key; renaming = deprecate-then-add-new with `--supersede`.
- **No semantic correctness oracle.** Validation gate is a smoke test. Authors who want assertions use the optional `expected` frontmatter field; deeper testing is out of scope.

## Adoption Checkpoint (benefit is falsifiable)

The library's value depends on actual reuse, which `snippets-stats.json` measures
for free. **Around 2026-09 (≈3 months after first ship), run `cs snippets stats`
on the adopting project(s) and decide:**

- If a healthy share of snippets show `successes >= 3`, the loop is paying for
  itself — keep everything.
- If most snippets sit at `successes < 3` (or the library never grew past a
  couple of entries), the distill/reuse discipline is not happening: cut the
  aging/prune/stats subsystem (~40% of the code) and keep only
  `search / show / use / add / deprecate`, rather than maintaining speculative
  machinery.

The empty-library fast path in `search` keeps the cold-start cost to a single
probe per session, so an unused library costs near-zero at runtime — the
checkpoint exists to cap the *maintenance* cost.

## Deliverables

| # | Change | Files |
|---|--------|-------|
| 1 | Type substitution + class wrapper + call generation | `cli/snippets/render.py` (new) |
| 2 | Snippet file IO + frontmatter parsing | `cli/snippets/store.py` (new) |
| 3 | Validation gate (read-only auto, mutates refusal, optional `expected`) | `cli/snippets/validate.py` (new) |
| 4 | Stats / audit IO + aging policy | `cli/snippets/stats.py` (new) |
| 5 | `cs snippets` subcommand wiring (9 subcommands) | `cli/cs.py` |
| 6 | Skill content | `skills/unity-cli-snippets/SKILL.md` (new) |
| 7 | Cross-reference updates in existing skills | `skills/unity-cli-command/SKILL.md`, `skills/unity-cli-exec-code/SKILL.md` |
| 8 | `.gitignore` addition for `snippets-stats.json` (auto-applied by `cs setup`) | `.gitignore` template + `cli/cs.py` |
| 9 | CLAUDE.md + READMEs | `CLAUDE.md`, `README.md`, `README_zh.md` |
| 10 | CHANGELOG entry under `[Unreleased]` | `CHANGELOG.md` |

## Execution Timeline

| Phase | Deliverables |
|-------|--------------|
| D0 | Render + Store + Validate (read-only) + Stats (init/read) modules; minimal CLI: `add` / `use` / `list` / `show`. End-to-end loop on a real Unity project, one hand-written snippet validated through `add` and called through `use`. |
| D1 | Remaining CLI: `search` / `update` / `deprecate` / `prune` / `stats`. Mutates-class refusal path. Aging policy (cold/broken classification). |
| D2 | `unity-cli-snippets` SKILL.md + cross-references in `unity-cli-command` / `unity-cli-exec-code`. Docs: CLAUDE.md, READMEs (EN+CN), CHANGELOG. `.gitignore` integration. |

D0 proves the architecture (the wrap-and-validate-and-execute loop) on the smallest possible surface before D1 expands it. Each phase is independently reviewable and shippable; D1 depends on D0; D2 depends on D1.
