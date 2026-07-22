# Unity CLI Setup

`cs setup` installs the C# Console package (`com.zh1zh1.csharpconsole`) into the Unity
project and version-checks it. Setup itself runs on pure stdlib — no Unity package needed
to run it.

**Ask the user before running setup.** Setup (and `--update`) writes
`Packages/manifest.json` — a shared, version-controlled project file. Never run it
unprompted: state what will be written and get the user's go-ahead first. The read-only
commands (`cs status`, `cs health`) never need consent.

## First-time install: ask which source shape

On a first-time install, don't silently take the default — offer the user an explicit
choice between the two source shapes setup can write:

1. **Git URL (default)** — the manifest gets the upstream git URL pinned to a version
   tag; Unity's Package Manager downloads it into its cache. Team-friendly (the same
   manifest entry works on every machine); the package source is read-only.
2. **Local clone + `file:`** — for editing/debugging the package source, or offline
   use. Ask the user **where to clone**, then do the clone yourself — don't require a
   pre-existing local copy:
   ```bash
   git clone https://github.com/niqibiao/unity-csharpconsole.git <dir>   # checkout the tag matching the CLI's major.minor
   cs setup --source "file:<dir>"
   ```
   **Caveat:** the manifest is shared and committed — an absolute `file:` path only
   works on this machine. Prefer a path relative to the project's `Packages/` folder
   (e.g. `file:../../Tools/unity-csharpconsole`), or make sure the user understands
   teammates will need the same layout.

## What setup does

1. Locates the Unity project (auto-detected; `--project` to override).
2. If the package is **absent** from `Packages/manifest.json`, adds it — the git URL by
   default, or `--source <url|file:path>` to override. `--update` forces Unity to
   re-resolve by removing and re-adding the entry. A git URL without a `#fragment` is
   **pinned to the newest upstream tag on the CLI's `major.minor` line** (e.g. CLI 2.0.1
   → `…git#v2.0.0`), so the manifest expresses the intended version instead of leaving
   only a commit hash in `packages-lock.json`. `file:` paths and URLs that already carry
   a `#fragment` are written as-is; if the tag query fails (offline), setup falls back to
   the unpinned URL with a warning.
3. If the package is **already present**, setup is a no-op that warns when the CLI and
   the installed package are on different `major.minor` lines.

```bash
cs setup
```

(`setup` output is plain text — no `--json`.)

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
