# Unity CLI Setup

`cs setup` does **not** install the Unity package — it locates the Unity project,
caches the resolved package path, and warns if the CLI and the installed package are
on different `major.minor` lines. (Every other command does the same locate+cache
lazily on first run, so `setup` is a convenience, not a gate.)

## 1. Make sure the Unity package is present

The C# Console package (`com.zh1zh1.csharpconsole`) must already be in the project.
Either:

- **Commit it with the skill** (team workflow): a maintainer adds the package once
  and commits it alongside the skill; everyone else just `git pull` and uses it.
- **Add it via Unity Package Manager** — "Add package from git URL":
  `https://github.com/niqibiao/unity-csharpconsole.git` — or add that git URL to
  `Packages/manifest.json` yourself.

Keep the package on the same `major.minor` as this skill's CLI (see `scripts/cli/VERSION`).

## 2. Locate + version-check

```bash
cs setup --json
```

It prints the project root and the resolved package path and runs the version check.
- `package: NOT FOUND` → the package isn't installed; add it (step 1) and re-run.
- `⚠ … version mismatch` → align the package version with the CLI, then re-run.

## 3. Verify the live service

Open the Unity Editor for this project and wait for the Package Manager to resolve
`com.zh1zh1.csharpconsole` and the C# Console service to start. Then run `cs status`
to confirm the service is reachable — `status` is the check, not a guarantee; report
whatever it returns.
