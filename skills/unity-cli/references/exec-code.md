# Unity CLI Exec Code (Fallback)

Execute raw C# in a running Unity Editor via the Roslyn-based CSharpConsole REPL.
Raw exec is the final fallback in the canonical routing order in `SKILL.md`.
Follow that routing and exhaust the matching built-in, cached custom command, and
snippet stages before using this reference.

After solving a non-trivial task that's likely to recur, consider distilling it
into a snippet — see `references/snippets.md`.

## Usage

Write the C# to a `.cs` file in the scratch dir — the absolute path
`<project-root>/Temp/CSharpConsole/AgentScratch/` (single source of truth: SKILL.md
"Passing parameters"; **never under `Assets/`** — a typical REPL snippet is not a
valid standalone project source, so the import very likely fails compilation, and
after an editor restart the console service may not start at all), then:

```bash
cs exec --file "<project-root>/Temp/CSharpConsole/AgentScratch/inspect-camera.cs"
```

Raw C# in a `.cs` file needs **zero escaping**. Never wrap code in a JSON
`{"code": …}` payload (`--input` exists for prebuilt requests piped via stdin, but
every quote / backslash / newline in the code must then be JSON-escaped — an easy
way to corrupt code).

Output is text: the REPL prints the last expression's value; errors go to stderr
with a non-zero exit code. Add `--json` only when you need the structured result
envelope.

The examples below show only the C# code — put it in the `.cs` file.

## REPL Features

This is a Roslyn REPL, not a simple eval. Non-obvious capabilities and limits:

- **Top-level syntax** — no `class`/`Main` boilerplate; write statements directly
- **Expression auto-return** — the last expression value is returned in the result; prefer over `Debug.Log`
- **Opt-in cross-call state** — without `--session`, each CLI invocation gets a
  fresh session. Reuse the same explicit `--session <id>` only when later calls
  intentionally depend on variables, `using`s, types, or helpers from earlier calls
- **Private member access** — the compiler normally bypasses
  `private`/`protected`/`internal` at compile time. A submission that triggers the
  visibility fallback described below uses standard C# accessibility instead
- **Pre-loaded usings** — `System` and `UnityEngine` are available by default. Add
  `using System.Linq;` or `using System.Collections.Generic;` explicitly when
  needed; a named session retains successfully compiled usings until that session
  is cleared. An invalid pure-`using` submission returns a compile error and is not
  retained; usings from any failed mixed submission are not retained either

## Patterns

### Expression evaluation

```csharp
DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")
// inline chains work too:
var cam = Camera.main; cam.fieldOfView
```

### Multi-step work — self-contained by default

For a simple operation, combine lookup + use in a single submission:

```csharp
var player = GameObject.Find("Player");
player.transform.position
```

When genuine incremental exploration is clearer, split the code into files and reuse
one task-specific id:

```bash
cs exec --session agent-a1b2c3 --file "<project-root>/Temp/CSharpConsole/AgentScratch/find-player.cs"
cs exec --session agent-a1b2c3 --file "<project-root>/Temp/CSharpConsole/AgentScratch/read-player.cs"
```

The second file may refer to variables or helpers declared by the first. Omit
`--session` again when the dependency ends; unrelated work should get a fresh context.

### Private member access and visibility fallback

```csharp
var go = GameObject.Find("Main Camera"); go.m_InstanceID
```

The REPL first compiles with accessibility checks disabled. If internal compatibility
types with the same name exist in multiple assemblies and produce CS0433, CS0104, or
CS0229, it automatically retries that submission with standard C# visibility. A
successful retry is prefixed with `[REPL NOTICE]`; only that submission loses access
to non-public members, and later submissions still try the normal private-access mode
first.

If one submission both references an ambiguous type and accesses a non-public member,
neither visibility mode may compile it. The error then includes
`[REPL ACTION REQUIRED]` with numbered instructions. Follow those instructions and
split the work into separate submissions; reuse the same explicit `--session` only
when the split submissions must share state. A plain ambiguity or unrelated compile
error does not require this split.

### LINQ queries over live scene

```csharp
// Unity 2022-compatible scene query.
using System.Linq; UnityEngine.Object.FindObjectsOfType<Rigidbody>().Select(r => $"{r.name}: mass={r.mass}").ToList()
// Resources.FindObjectsOfTypeAll is still current — it returns inactive/asset objects too:
using System.Linq; Resources.FindObjectsOfTypeAll<GameObject>().Where(g => !g.activeInHierarchy).Select(g => g.name).ToList()
```

### AssetDatabase

```csharp
using System.Linq; UnityEditor.AssetDatabase.FindAssets("t:Material").Select(g => UnityEditor.AssetDatabase.GUIDToAssetPath(g)).ToList()
```

### Define and use a helper

For one-shot work, define and invoke the helper in one submission:

```csharp
string Dump(Transform t, int d=0) { var s = new string(' ', d*2) + t.name; foreach(Transform c in t) s += "\n" + Dump(c, d+1); return s; }
Dump(GameObject.Find("Canvas").transform)
```

A named session can retain the helper across dependent calls. It remains ephemeral;
a helper worth reusing across tasks belongs in the snippet library.

### Batch modify

```csharp
foreach(var r in GameObject.FindGameObjectsWithTag("Debug").SelectMany(g => g.GetComponents<MeshRenderer>())) r.enabled = false;
```

## Session Reset

The simplest reset is to stop passing the old id or choose a new one. To explicitly
reset a named session, write the normal command request
`{"ns":"session","action":"reset","args":{}}` to the scratch JSON file and run:

```bash
cs command --session agent-a1b2c3 --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req-reset-session.json"
```

Domain reload also clears all REPL sessions. Session state is working context, never
durable storage.

## Notes

- `exec` output is text; the process exit code carries success/failure. Use `--json`
  only when the structured envelope is needed (then check `ok` / `exitCode`)
- Port is auto-detected from `Temp/CSharpConsole/refresh_state.json`
