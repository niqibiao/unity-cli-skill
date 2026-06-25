# Unity CLI Snippets

## Decision Order (strict)

Before writing ad-hoc `cs exec` for any non-trivial Unity automation:

1. `cs list-commands [--type custom]` — built-in or custom command available?
2. `cs snippets search <description>` — matching snippet?
3. Only if neither match: ad-hoc `cs exec`.

"Non-trivial" = >3 lines, or uses LINQ / reflection / AssetDatabase / multi-step.

**Empty-library fast path:** if `search` returns `libraryEmpty: true`, skip step 2
for the rest of the session and go ad-hoc directly — resume searching once you
`add` the first snippet.

**Never** `Read` or `ls` `.unity-cli/snippets~/` directly. Always go through the CLI.

## Workflow

```
search → (show) → use → (distill if reusable)
```

## CLI Quick Reference

The 5 you'll actually use:

| Command | Purpose |
|---------|---------|
| `cs snippets search <q>` | Top-N lexical hits over id + summary |
| `cs snippets show <id>` | Full body and metadata for one snippet |
| `cs snippets use <id> --args '<json>'` | Run with typed args; tracks stats |
| `cs snippets add <id> --file <md>` | Validate and register a new snippet |
| `cs snippets deprecate <id> [--supersede <new>]` | Retire a snippet without deletion |

Full set: `list / show / search / use / add / update / deprecate / prune / stats / doctor` — see `cs snippets --help`. For library health audits and anti-rot cleanup (`doctor`), use the `cs snippets doctor` skill.

## Snippet Anatomy

````markdown
---
id: scene.find_active_in_layer
summary: Find active GameObjects in a specific layer
safety: read-only
args:
  - name: layerName
    type: string
example:
  layerName: "Default"
---

```csharp
using System.Linq;

static string Run(string layerName) {
    return string.Join(",", UnityEngine.Object.FindObjectsOfType<GameObject>()
        .Where(g => g.activeInHierarchy && LayerMask.LayerToName(g.layer) == layerName)
        .Select(g => g.name));
}
```
````

An optional `expected: "<string>"` frontmatter field makes validation also compare
the textual REPL result (the `ToString()` of `Run`'s return value) — return a
string (as above) when you want that assertion to be meaningful.

- Body must define one `static Run(...)` method matching `args` order.
- Helpers go alongside `Run` as `static` members. No local functions.
- The CLI wraps body+call in a unique class name on submission; symbols don't leak across snippets.

## When to Distill

All must hold:

- Code is parameterized (or trivially can be parameterized into 1–4 typed args).
- Solves a recurring concept: query, batch op, common workflow.
- User signaled "save this", OR you judge the pattern likely to recur.

## When NOT to Distill

Any one disqualifies:

- One-shot tied to an exact path/name/id.
- Trivial enough that the snippet wrapper isn't shorter than the original.
- Depends on ephemeral or generated symbols (autogen code, runtime-injected types) that won't exist after a fresh checkout.
- Half-working / WIP.

## Safety Classes

- **`read-only`** — pure query, auto-validated by `add` / `update --file`.
- **`mutates`** — side effects on scene, assets, files, settings. Cannot be auto-validated. Requires `--no-validate`; audit marks `unverified: true` and `list` prefixes the row with UNVERIFIED.

Snippets that touch `AssetDatabase`, write files, change `ProjectSettings`, trigger refreshes, or affect domain reload are always `mutates`.

## Validation Gate

`add` (and `update --file`) runs the snippet's `example` once through the REPL:

- `read-only`: must return `ok=true`. With the optional `expected:` string, the textual result (the `ToString()` of `Run`'s return value) must also match. The REPL never returns structured JSON — have `Run` return a formatted string when you want a meaningful assertion.
- `mutates`: refused unless `--no-validate`.

The gate is a **smoke test**, not a correctness oracle. Use `expected:` for result assertions.

## Argument Types

| Type | JSON shape | Generated literal |
|------|-----------|-------------------|
| `string` | `"foo"` | `"foo"` (escaped) |
| `int` / `float` / `bool` | `42` / `3.14` / `true` | `42` / `3.14f` / `true` |
| `vector2` / `vector3` / `vector4` | `[x, y, ...]` | `new UnityEngine.Vector3(x, y, z)` |
| `color` | `[r, g, b]` or `[r, g, b, a]` | `new UnityEngine.Color(r, g, b, a)` |
| `string[]` / `int[]` / `float[]` | `[...]` | `new T[] { ... }` |

No `Quaternion` (use `vector3` Euler or `vector4` raw inside `Run`). No `expr` (build the expression inside `Run`). Optional args declare a `default:` field.

## Aging

`stats` fields track `successes`, `failures`, `last_used`, `consecutive_failures`. Only Run-body errors count as failures — environment errors (Unity not running, network) never do. A snippet is auto-deprecated at `use` time only when `consecutive_failures >= 5` AND the streak spans ≥ 7 days. Cold detection (low usage / old) is **informational** in `list --sort cold`; `prune --cold` is opt-in and default `prune` never touches live snippets.

## DO NOT

- Read `.unity-cli/snippets~/` directly with shell tools.
- Hand-edit snippet `.md` files; use `add` / `update --file` so validation runs.
- Skip `cs list-commands` and `cs snippets search` before ad-hoc `cs exec`.
- Distill one-shot operations or trivial one-liners.

## Boundary with `cs command` / `cs exec`

- Built-in/custom command available → `cs command` (see `cs command` skill).
- One-off ad-hoc → `cs exec` (see `cs exec` skill).
- Reusable ad-hoc → `cs snippets`.
