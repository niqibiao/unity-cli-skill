# Unity CLI Refresh

Trigger Unity to re-scan assets and recompile scripts. Use after writing or
modifying `.cs` files on disk.

**Recommended (one-step):**

```bash
cs refresh --exit-playmode --wait 120
```

- `--exit-playmode` automatically exits play mode before refreshing if needed
- `--wait TIMEOUT` blocks until the refresh + compile + domain-reload cycle completes (default 120s, max 600s)
- Domain reload restarts the HTTP service and clears REPL sessions; `--wait` handles reconnection

After completion, verify with `cs status` if needed.

**Manual control (when you need fine-grained steps):**

1. Check play mode — `req.json`: `{"ns":"editor","action":"playmode.status"}`:
```bash
cs command --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req.json"
```

2. If `isPlaying: true` and you need to exit first — `{"ns":"editor","action":"playmode.exit"}`:
```bash
cs command --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req.json"
```

3. Trigger refresh without `--exit-playmode`:
```bash
cs refresh --wait 120
```
