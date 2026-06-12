---
description: "Audit the built-in command tables in SKILL.md against the live Unity Editor"
---

**Audience:** plugin maintainers. This command checks whether the static built-in tables in `unity-cli-command/SKILL.md` have drifted from the commands registered in the running Editor (new actions added upstream, removed actions, changed signatures). It does **not** touch the per-project custom-command cache — for that, run `/unity-cli-refresh-commands`.

Steps:

1. Fetch the live command list:
```bash
python "${CLAUDE_PLUGIN_ROOT}/cli/cs.py" list-commands --json --project "$(pwd)"
```

2. Parse `data.commands` from the JSON output. This contains all registered commands (built-in + custom).

3. Compare with the static catalog in `${CLAUDE_PLUGIN_ROOT}/skills/unity-cli-command/SKILL.md`. The built-in namespaces are: editor, gameobject, component, transform, material, prefab, project, scene, screenshot, profiler, session, command.

4. Report differences:
   - **New commands** not in SKILL.md → suggest adding them
   - **Removed commands** in SKILL.md but not live → suggest removing them
   - **Changed signatures** (different args) → suggest updating

5. If custom commands exist outside the built-in namespaces, also run `/unity-cli-refresh-commands` to update the custom command cache.
