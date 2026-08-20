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
cs pull "C:/Users/me/AppData/LocalLow/Studio/Game/logs/game.log" --mode runtime
```

Path resolution, reported on every successful pull so it is never a guess:

- **relative** — resolved against the target's `persistentDataPath`
- **absolute, already under that directory** — used as given
- **absolute from a different machine** — the tail after the product folder is
  re-anchored to the target's `persistentDataPath`. This is the case where a
  path was read off a desktop for a file that actually lives on a phone.

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
