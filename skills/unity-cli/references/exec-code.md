# Unity CLI Exec Code (Fallback)

Execute raw C# in a running Unity Editor via the Roslyn-based CSharpConsole REPL.
Always prefer the `cs command` skill first.

Then check the snippet library (`cs snippets search <description>`) before writing ad-hoc code. After solving a non-trivial task that's likely to recur, consider distilling it into a snippet — see the `cs snippets` skill.

## Usage

Inline code:

```bash
cs exec --json "<C# code>"
```

From a file (avoids shell quoting hazards for long/complex snippets):

```bash
cs exec --json --file path/to/snippet.cs
```

All examples below use the inline form, showing only the C# code portion for brevity.

## REPL Features

This is a Roslyn REPL, not a simple eval. Non-obvious capabilities:

- **Top-level syntax** — no `class`/`Main` boilerplate; write statements directly
- **Expression auto-return** — the last expression value is returned in the result; prefer over `Debug.Log`
- **Cross-submission state** — variables, `using`s, and types persist across `exec` calls within the session
- **Private member access** — compiler bypasses `private`/`protected`/`internal` at compile time
- **Pre-loaded usings** — `System` and `UnityEngine` are available by default. Add `using System.Linq;` or `using System.Collections.Generic;` explicitly when needed (they persist in the session)

## Patterns

### Expression evaluation

```csharp
DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")
// inline chains work too:
var cam = Camera.main; cam.fieldOfView
```

### Multi-step with cross-submission state

```csharp
// Call 1: store a reference
var player = GameObject.Find("Player");
```

```csharp
// Call 2: `player` is still alive
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
// System.Linq persists — no need to re-import
Resources.FindObjectsOfTypeAll<GameObject>().Where(g => !g.activeInHierarchy).Select(g => g.name).ToList()
// Resources.FindObjectsOfTypeAll is still current — it returns inactive/asset objects too.
```

### AssetDatabase

```csharp
using System.Linq; UnityEditor.AssetDatabase.FindAssets("t:Material").Select(g => UnityEditor.AssetDatabase.GUIDToAssetPath(g)).ToList()
```

### Define reusable helpers (persists in session)

```csharp
// Call 1: define a function
string Dump(Transform t, int d=0) { var s = new string(' ', d*2) + t.name; foreach(Transform c in t) s += "\n" + Dump(c, d+1); return s; }
// Call 2: use it — function persists across submissions
Dump(GameObject.Find("Canvas").transform)
```

### Batch modify

```csharp
foreach(var r in GameObject.FindGameObjectsWithTag("Debug").SelectMany(g => g.GetComponents<MeshRenderer>())) r.enabled = false;
```

## Session Reset

Reset when variable name collisions or stale state occur:

```bash
cs command --json session reset
```

## Notes

- Always use `--json` for parseable output
- Check `result.ok` and `result.exitCode` for success/failure
- Port is auto-detected from `Temp/CSharpConsole/refresh_state.json`
