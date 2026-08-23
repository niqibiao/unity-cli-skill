# Debugging a Running Player

`--mode runtime` addresses the player instead of the editor. Use it when the
question is about a build that is running — on this machine, on a colleague's,
or on a device — rather than about the editor.

```bash
cs health --mode runtime               # is the player's service alive?
cs command -i - --mode runtime         # any command the player answers
cs pull <path> --mode runtime -o out   # retrieve a file from the player
cs exec --mode runtime --code "..."    # compile in the editor, run in the player
```

Add `--ip <address>` for a player on another machine. Without `--port`, the CLI
probes 15500-15509 (all at once, 1s each) because a player writes no port file.
If nothing answers there, commands that need the player exit 3 and say so rather
than falling back to the editor.

**The editor has to be running too**, except for `cs health` and `cs pull`.
Every canonical command is checked against the package-owned registry before it
is sent, and the editor is the registry's authority — with it closed, `cs
command` and `cs batch` refuse rather than dispatch against an unverifiable
contract. So debugging a player still means a live editor on the project.

## What the player answers

23 of the 62 commands. The rest need the editor and say so if asked:

- `runtime/info` — device, build, and where files live
- `scene/hierarchy`, `gameobject/*`, `transform/set`, `component/get|modify`
- `screenshot/game_view`, `profiler/start|stop|status`
- `session/*`, `command/*`

Two limits. A player has no undo stack, so `gameobject/destroy`, `transform/set`
and the other mutations **cannot be reversed** — be more careful than in the
editor, especially against someone else's machine. And `component/get|modify`
covers only components the project declares: built-in ones such as `Transform`
or `Renderer` keep their state in native properties rather than serialized
fields, so the player refuses them instead of reporting a different shape than
the editor does.

`cs batch --mode runtime` reaches the player as a single roundtrip, so
`command/list` and `command/registry.snapshot` — which the editor owns — are
refused inside one. Request those separately without `--mode runtime`.

`cs refresh`, `cs doctor`, `cs wait-ready` and `cs test` always target the
editor; `--mode runtime` does not change them, because compilation is the
editor's job. `cs exec --mode runtime` still compiles in the editor and executes
the result in the player, so both must be reachable.

## Retrieving files

`cs pull` fetches a file from whichever process is being addressed — the player
under `--mode runtime`, otherwise the editor.

```bash
cs command -i - --mode runtime          # {"id":"runtime/info","args":{}}
cs pull logs/game.log --mode runtime -o game.log
```

Path resolution, reported on every successful pull so it is never a guess:

- **relative** — resolved against the target's `persistentDataPath`. Use this
  form for a target on another machine or a device; on Android and iOS there is
  no absolute path to hold in the first place.
- **absolute** — a path on the target itself, used as given. It is never
  rewritten to point somewhere else, so a path under the player's own install
  directory works even when that directory is named after the product.

One response is capped at 32MB; `cs pull` requests successive ranges on its own,
so file size is not a limit.

## Getting a device's logs

`runtime/info` reports `consoleLogPath`, but **on Android and iOS it is empty**,
and that is correct: those platforms write no log file. Android sends output to
logcat and iOS to the device console. Standalone players (Windows, macOS, Linux)
do write `Player.log` and it can be pulled directly.

So on a device, ask the user where their project writes its own log, and pull
that. Do not report "no logs available" — report that the platform keeps none of
its own and ask what the project writes.

To watch a log as it is written rather than fetching a snapshot:

```bash
cs logs --mode runtime                       # the target's own consoleLogPath
cs logs logs/game.log --mode runtime --wait 120
cs logs --since-start > player.log           # from the beginning of the file
```

`cs logs` prints new bytes as they appear and returns when `--wait` expires
(default 60s, capped at 600s) — bound it deliberately, because an agent has no
Ctrl+C to press. Without a path it follows `consoleLogPath`, and on a platform
that reports none it says so instead of following nothing. When the file becomes
shorter than the read position — Unity rotates `Player.log` to `Player-prev.log`
at startup — it reports the restart on stderr and re-reads the new file from its
beginning, so nothing is silently skipped. Like `cs pull`, it reads bytes rather
than running a command, so it works with the editor closed.

## Collecting in the background

To keep watching while you carry on issuing commands:

```bash
cs logs --mode runtime --background     # prints the file it collects into
cs command -i - --mode runtime          # …other work, unaffected
cs logs --mode runtime --stop           # end it early
```

`--background` returns immediately with the path of a file under the system
temp directory, and a detached follower keeps appending to it. Read that file
whenever you want — it is an ordinary file. Collection runs until **the target
goes away**, so a player that runs for hours is covered for all of them; there
is no clock to re-arm. One dropped connection does not end it, because the
service drops one occasionally; only being unable to reach the target for 15
seconds straight does, and that is reported in the file.

Starting it twice for the same target returns the collection already running
rather than starting a second reader. Commands issued while it collects behave
as they do with no collection running.

## Recording a profile on a device

The one workflow that was previously impossible:

```bash
# 1. start recording into a file the device can write
cs command -i - --mode runtime   # profiler/start with logFile under persistentDataPath
# 2. exercise the build
# 3. stop, then bring the capture home
cs command -i - --mode runtime   # profiler/stop
cs pull <that logFile> --mode runtime -o capture.raw
```

Open `capture.raw` in the Profiler window. `profiler/save` stays editor-only —
it needs `ProfilerDriver` — but it is not needed here, because `profiler/start`
writes the binary log itself.

Recording only works in development builds; a release build ignores it.

## Trust model

The service binds all interfaces without authentication, by design, so that
teammates' editors and players can be reached across a trusted LAN. `cs pull`
reads any path the target can read, which grants nothing new — a session can
already read any file by executing C#. Do not expose these ports to untrusted
networks.
