# Unity CLI Status

Check the current state of the Unity C# Console plugin: package installation and
service connectivity.

Run:

```bash
cs status
```

Reports:
- **project**: Unity project root path
- **package**: whether `com.zh1zh1.csharpconsole` is installed and resolvable
- **service**: whether the Unity HTTP service is reachable at the configured port

**Version mismatch handling:** if the output contains `⚠` indicating
plugin/package version misalignment, do NOT just report the mismatch. Directly
ask the user whether they want to update the package now. If they confirm, run
the **cs setup** skill (with `--update`) to perform the update.

**Reporting:** report only what `status` returned. When suggesting next steps, do
NOT invent CLI subcommands — raw C# is `cs exec`, there is no `cs run`. If unsure
which subcommands exist, check `cs --help` — not `cs list-commands`, which lists
Unity framework commands and needs the editor service running.
