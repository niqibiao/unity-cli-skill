---
name: unity-cli-status
description: >
  Show Unity C# Console package installation and service connection status. Use
  when checking unity-cli status, whether the com.zh1zh1.csharpconsole package is
  installed/resolvable, whether the Unity Editor HTTP service is reachable, or the
  user asks to check status / 状态 / 连接. Works in both Claude Code and Codex.
---

# Unity CLI Status

Check the current state of the Unity C# Console plugin: package installation and
service connectivity.

> **Running `cs`:** below, `cs` is shorthand for
> `python "$HOME/.unity-cli-plugin/current/cli/cs.py"` — one stable path, run
> verbatim without changing directory. If it's not installed yet, run the
> **unity-cli-setup** skill once first.

Run:

```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" status --project "$(pwd)"
```

Reports:
- **project**: Unity project root path
- **package**: whether `com.zh1zh1.csharpconsole` is installed and resolvable
- **service**: whether the Unity HTTP service is reachable at the configured port

**Version mismatch handling:** if the output contains `⚠` indicating
plugin/package version misalignment, do NOT just report the mismatch. Directly
ask the user whether they want to update the package now. If they confirm, run
the **unity-cli-setup** skill (with `--update`) to perform the update.

**Reporting:** report only what `status` returned. When suggesting next steps, do
NOT invent CLI subcommands — raw C# is `cs exec`, there is no `cs run`. If unsure
what's available, run `cs list-commands` rather than guessing.
