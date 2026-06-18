---
name: unity-cli-refresh-commands
description: >
  Sync the per-project custom command catalog from the running Unity Editor (cs
  catalog sync). Use after registering new C# framework commands in Unity, when
  `cs catalog list` looks stale/empty, or when the user asks to refresh the custom
  command list / 刷新命令. Works in both Claude Code and Codex.
---

# Unity CLI Refresh Commands

Sync the custom command catalog from the running Unity Editor.

> **Running `cs`:** below, `cs` is shorthand for
> `python "$HOME/.unity-cli-plugin/current/cli/cs.py"` — one stable path, run
> verbatim without changing directory. If it's not installed yet, run the
> **unity-cli-setup** skill once first.

Run:

```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" catalog sync --json --project "$(pwd)"
```

Parse the JSON output. Report the summary (added/removed/total) and the catalog
file path from `data.catalogFile`.

On first sync for a project, the catalog file defaults to
`{project}/.unity-cli/catalog.json` (the path is cached for subsequent runs). To
pick a different location once, run:

```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" catalog sync --json --project "$(pwd)" --catalog-path /your/path/catalog.json
```

If the command fails, suggest the user check that Unity Editor is open and the C#
Console package is installed.
