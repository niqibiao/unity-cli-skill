# Unity CLI Setup

`cs setup` installs the C# Console package (`com.zh1zh1.csharpconsole`) into the Unity
project and version-checks it. Setup itself runs on pure stdlib — no Unity package needed
to run it.

## What setup does

1. Locates the Unity project (auto-detected; `--project` to override).
2. If the package is **absent** from `Packages/manifest.json`, adds it — the git URL by
   default, or `--source <url|file:path>` to override. `--update` forces Unity to
   re-resolve by removing and re-adding the entry. The source is written as-is — no
   version pin.
3. If the package is **already present**, setup is a no-op that warns when the CLI and
   the installed package are on different `major.minor` lines.

```bash
cs setup --json
```

Every other command also does this locate + cache lazily on first run, so `setup` is a
convenience, not a gate.

## After setup: resolve in Unity

setup only writes the manifest entry — the user must **open the Unity Editor for this
project** so the Package Manager downloads / resolves `com.zh1zh1.csharpconsole` and the
C# Console service starts. Hand this off in plain language: tell the user to open Unity,
wait for it to finish compiling, then **check unity-cli status**. Re-run `cs status`
yourself to confirm the service is reachable — don't paste the raw command (or `--json`)
for the user to type; that's agent-internal.

- `package: NOT FOUND` after setup → Unity hasn't resolved it yet; open/focus the Editor,
  wait for compilation, then re-check.
- `⚠ … version mismatch` → align the package with the CLI: re-resolve with
  `cs setup --update`, or point `--source` at a matching version/tag, then re-run
  `cs status`.

Keep the package on the same `major.minor` as this skill's CLI (see `scripts/cli/VERSION`).
