# Unity CLI Exec Code (Fallback)

Execute raw C# in a running Unity Editor via the Roslyn-based CSharpConsole REPL.
Always prefer `cs command` first.

Then check the snippet library (`cs snippets search <description>`) before writing ad-hoc code. After solving a non-trivial task that's likely to recur, consider distilling it into a snippet — see references/snippets.md.

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
- **No cross-call state** — every CLI invocation is its own REPL session (a fresh
  session id per run): variables, `using`s, types, and helpers do **not** survive
  from one `cs exec` to the next. Send complete, self-contained code each call
- **Private member access** — compiler bypasses `private`/`protected`/`internal` at compile time
- **Pre-loaded usings** — `System` and `UnityEngine` are available by default. Add `using System.Linq;` or `using System.Collections.Generic;` explicitly when needed (in every call that uses them)

## Patterns

### Expression evaluation

```csharp
DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")
// inline chains work too:
var cam = Camera.main; cam.fieldOfView
```

### Multi-step work — one self-contained call

There is no state between CLI calls, so combine lookup + use in a single submission:

```csharp
var player = GameObject.Find("Player");
player.transform.position
```

### Private member access (no reflection needed)

```csharp
var go = GameObject.Find("Main Camera"); go.m_InstanceID
```

### LINQ queries over live scene

```csharp
// Prefer FindObjectsByType (Unity 2023.1+); FindObjectsOfType is deprecated.
using System.Linq; UnityEngine.Object.FindObjectsByType<Rigidbody>(FindObjectsSortMode.None).Select(r => $"{r.name}: mass={r.mass}").ToList()
// Resources.FindObjectsOfTypeAll is still current — it returns inactive/asset objects too:
using System.Linq; Resources.FindObjectsOfTypeAll<GameObject>().Where(g => !g.activeInHierarchy).Select(g => g.name).ToList()
```

### AssetDatabase

```csharp
using System.Linq; UnityEditor.AssetDatabase.FindAssets("t:Material").Select(g => UnityEditor.AssetDatabase.GUIDToAssetPath(g)).ToList()
```

### Define and use a helper — in the same call

Helpers do not survive to the next CLI call: define and invoke them in one
submission. A helper worth keeping belongs in the snippet library, not in REPL state:

```csharp
string Dump(Transform t, int d=0) { var s = new string(' ', d*2) + t.name; foreach(Transform c in t) s += "\n" + Dump(c, d+1); return s; }
Dump(GameObject.Find("Canvas").transform)
```

### Batch modify

```csharp
foreach(var r in GameObject.FindGameObjectsWithTag("Debug").SelectMany(g => g.GetComponents<MeshRenderer>())) r.enabled = false;
```

## Session Reset

Not needed through the CLI: each invocation already starts a fresh REPL session, so
there is no stale state to reset (and `session/reset` cannot target a previous
call's session — its id was random and is gone). The reset command only matters for
long-lived REPL clients holding one session open.

## Notes

- `exec` output is text; the process exit code carries success/failure. Use `--json`
  only when the structured envelope is needed (then check `ok` / `exitCode`)
- Port is auto-detected from `Temp/CSharpConsole/refresh_state.json`
