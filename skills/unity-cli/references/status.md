# Unity CLI Status

Check the current state of the Unity C# Console service: package installation and
service connectivity.

Run:

```bash
cs status
```

Reports:
- **project**: Unity project root path
- **package**: whether `com.zh1zh1.csharpconsole` is installed and resolvable
- **service**: whether the Unity HTTP service is reachable at the configured port
- **version**: package/protocol/Unity versions from the live service, or the
  on-disk package version when the service is down

Exit code 0 means **fully operational** (service reachable and healthy); any
degraded state — no project, package missing, service unreachable — exits 1.
Read the text to see which layer is down.

**Version mismatch handling:** if the output contains `⚠` indicating CLI/package
version misalignment, do NOT just report the mismatch. Explain that the installed Unity
package and the bundled CLI are on different `major.minor` lines, and ask the user to
align the package — update it in Unity (Package Manager, or bump the git tag / version
in their project) to match the CLI, then re-run `cs status` to confirm.

**Reporting:** report only what `status` returned. When suggesting next steps, do
NOT invent CLI subcommands — raw C# is `cs exec`, there is no `cs run`. If unsure
which subcommands exist, check `cs --help` — not `cs list-commands`, which lists
Unity framework commands and needs the editor service running.
