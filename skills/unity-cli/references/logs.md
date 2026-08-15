# Unity CLI Editor Log

Read the Editor log directly. `editor/console.mark` returns `logPath`, and the
file stays readable while the Editor holds it open, so a local read is the whole
mechanism. There is no command that fetches log text.

**These rules locate and label. They never decide what to read.** Read
everything after your marker; use the rules to see the important part first, to
avoid reading one record as two, and to avoid counting one compile error three
times. A line no rule explains is a line to read, not to skip — every rule below
came from a case where a narrower filter dropped something that mattered.

## Bound the region

```bash
# req.json: {"id":"editor/console.mark","args":{"label":"before-refresh"}}
cs command --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req.json"
```

The result carries `logPath` and `markerText`. Do the work, then read from the
marker to end of file:

```bash
grep -n "<marker-id>" "<logPath>"          # gives the line number
tail -n +<line> "<logPath>"
```

Bound the region by that marker, not by a pattern. A marker is a fact about
time; a pattern is a guess about what the answer looks like.

For a long session, start the Editor with its own log so the region is not
interleaved with other runs:

```bash
Unity.exe -projectPath <project> -logFile <path>/session.log
```

## Records

A record starts at a line prefixed `[YYYY-MM-DD HH:MM:SS] `. Every line after it
without that prefix belongs to it — message continuation, stack frames, indented
timing trees, all of it.

The prefix is not guaranteed by every Editor build. If a log has none, fall back
to reading the region as plain text rather than splitting it wrongly.

## Stack traces

`Application.SetStackTraceLogType` gives each `LogType` one of three modes, and
the record's shape changes completely between them. The stack begins at one of
three anchors:

| Mode | First stack line |
|---|---|
| `ScriptOnly` (default) | `UnityEngine.StackTraceUtility:ExtractStackTrace ` |
| `Full` | `0x<hex> (Unity) StackWalker::GetCurrentCallstack` |
| `None` | no stack; the record is the head line alone |

`Debug.LogException` ignores the mode. Its stack is always managed-only and
starts at `UnityEngine.DebugLogHandler:Internal_LogException`.

Everything from the anchor to the end of the record is stack. Do not try to
recognise frames one at a time — they range from `Type:Method (args) (at
file:line)` through generic forms like ``AsyncTaskMethodBuilder`1<object>:Start<
Outer/<Inner>d__0>`` to bare native symbols such as `0x<hex> (mono-2.0-bdwgc)
mono_jit_set_domain`.

Under `Full` a record runs to roughly 48 lines for a single `Debug.Log`.

## Log type

The frame naming the type sits inside the stack. Under `Full` it is not at the
start of its line, so do not anchor the match:

```
(?:^|\) )UnityEngine\.Debug:Log(Warning|Error|Assertion|Exception)?[ (]
```

| Frame | LogType |
|---|---|
| `UnityEngine.Debug:Log (object)` | `Log` |
| `UnityEngine.Debug:LogWarning (object)` | `Warning` |
| `UnityEngine.Debug:LogError (object)` | `Error` |
| `UnityEngine.Debug:LogAssertion (object)` | `Assert` |
| `UnityEngine.Debug:LogException(Exception)` | `Exception` |

`LogException` has no space before its parenthesis and its user frames carry no
`(at file:line)` suffix. A pattern requiring `\s\(` misses the whole class.

### When the type cannot be determined

- **No stack.** Under `None` only the head line survives. A head shaped
  `SomeException: message` is still an exception; nothing else is decidable.
- **A stack with no `Debug:Log*` frame.** The engine logged this itself rather
  than through `Debug.*`. **Read these first.** The most consequential record in
  the log used to write this reference was one of them:

  ```
  Serialization depth limit 10 exceeded at 'AssetHierarchyNode.components'.
  ```

  A filter keyed on `Error` or `Exception` discards it.

## Compiler diagnostics

```
<file>.cs(<line>,<col>): error CS####: <message>
```

The same diagnostic is written three times — once in the raw build output, once
under a `## Script Compilation Error for:` header, once more with a timestamp.
Deduplicate on `(file, line, column, code)`. That is a merge, not a filter; keep
one copy of each.

## Noise

Collapse, do not delete. These carry nothing on their own but sit between things
that do:

- csc response file arguments: `-r:"…"`, `-define:…`, `/nowarn:…` — hundreds of
  lines per compile
- build progress: `[ 12/34   0s] …`, `*** Tundra build …`
- device polling: `Scanning for USB devices`
- indented engine timing trees
- output from co-hosted processes, e.g. `info: Microsoft.…`

## Reading a refresh

`cs refresh --wait` can report

```
Timed out waiting for Unity service recovery: Script compilation finished, waiting for reload or idle
```

while the compile actually succeeded and the reload merely finished after the
timeout. Confirm in the log before acting on the failure:

```bash
grep -nE "Tundra build (success|failed)|## Script Compilation Error|Reloading assemblies" "<logPath>" | tail -5
```

`editor/status` can also hold a stale `compiling` / `compileFailed` for minutes
after the work is done. The log is the authority.
