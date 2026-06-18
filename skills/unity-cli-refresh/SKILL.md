---
name: unity-cli-refresh
description: >
  Trigger Unity AssetDatabase refresh and wait for script compilation. Use after
  writing or modifying .cs files / assets on disk so Unity recompiles, or when the
  user asks to refresh / recompile / 刷新 / 重编译 Unity. Works in both Claude Code
  and Codex.
---

# Unity CLI Refresh

Trigger Unity to re-scan assets and recompile scripts. Use after writing or
modifying `.cs` files on disk.

> **Running `cs`:** below, `cs` is shorthand for
> `python "$HOME/.unity-cli-plugin/current/cli/cs.py"` — one stable path, run
> verbatim without changing directory. If it's not installed yet, run the
> **unity-cli-setup** skill once first.

**Recommended (one-step):**

```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" refresh --json --project "$(pwd)" --exit-playmode --wait 120
```

- `--exit-playmode` automatically exits play mode before refreshing if needed
- `--wait TIMEOUT` blocks until the refresh + compile + domain-reload cycle completes (default 120s, max 600s)
- Domain reload restarts the HTTP service and clears REPL sessions; `--wait` handles reconnection

After completion, verify with the **unity-cli-status** skill if needed.

**Manual control (when you need fine-grained steps):**

1. Check play mode:
```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" command --json --project "$(pwd)" editor playmode.status
```

2. If `isPlaying: true` and you need to exit first:
```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" command --json --project "$(pwd)" editor playmode.exit
```

3. Trigger refresh without `--exit-playmode`:
```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" refresh --json --project "$(pwd)" --wait 120
```
