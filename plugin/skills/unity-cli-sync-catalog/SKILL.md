---
name: unity-cli-sync-catalog
description: >
  Maintainer-only audit: check whether the static built-in command tables in the
  unity-cli-command skill have drifted from the live Unity Editor (new/removed
  actions, changed signatures). Use ONLY when a plugin maintainer explicitly asks
  to audit / sync the built-in command catalog against the Editor. For normal
  custom-command refresh use the unity-cli-refresh-commands skill instead. Works
  in both Claude Code and Codex.
---

# Unity CLI Sync Catalog (maintainer)

**Audience:** plugin maintainers. This audits whether the static built-in tables
in the `unity-cli-command` skill have drifted from the commands registered in the
running Editor (new actions added upstream, removed actions, changed signatures).
It does **not** touch the per-project custom-command cache — for that, use the
**unity-cli-refresh-commands** skill.

> **Running `cs`:** below, `cs` is shorthand for
> `python "$HOME/.unity-cli-plugin/current/cli/cs.py"` — one stable path, run
> verbatim without changing directory. If it's not installed yet, run the
> **unity-cli-setup** skill once first.

Steps:

1. Fetch the live command list:
```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" list-commands --json --project "$(pwd)"
```

2. Parse `data.commands` from the JSON output. This contains all registered commands (built-in + custom).

3. Compare with the static catalog in the `unity-cli-command` skill's `SKILL.md`. The built-in namespaces are: editor, gameobject, component, transform, material, prefab, project, scene, screenshot, profiler, session, command.

4. Report differences:
   - **New commands** not in SKILL.md → suggest adding them
   - **Removed commands** in SKILL.md but not live → suggest removing them
   - **Changed signatures** (different args) → suggest updating

5. If custom commands exist outside the built-in namespaces, also use the **unity-cli-refresh-commands** skill to update the custom command cache.
