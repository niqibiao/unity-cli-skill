# Unity CLI Refresh

Trigger Unity to re-scan assets and recompile scripts. Use after writing or
modifying `.cs` files on disk.

**Recommended (one-step):**

```bash
cs refresh --json --exit-playmode --wait 120
```

- `--exit-playmode` automatically exits play mode before refreshing if needed
- `--wait TIMEOUT` blocks until the refresh + compile + domain-reload cycle completes (default 120s, max 600s)
- Domain reload restarts the HTTP service and clears REPL sessions; `--wait` handles reconnection

After completion, verify with the **cs status** skill if needed.

**Manual control (when you need fine-grained steps):**

1. Check play mode:
```bash
cs command --json editor playmode.status
```

2. If `isPlaying: true` and you need to exit first:
```bash
cs command --json editor playmode.exit
```

3. Trigger refresh without `--exit-playmode`:
```bash
cs refresh --json --wait 120
```
