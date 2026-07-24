# Unity CLI Refresh

Trigger Unity to re-scan assets and recompile scripts. Use after writing or
modifying `.cs` files on disk.

**Recommended (one-step):**

```bash
cs refresh --exit-playmode --wait 120
```

- `--exit-playmode` automatically exits play mode before refreshing if needed
- `--wait TIMEOUT` blocks until the refresh + compile + domain-reload cycle completes (default 60s, max 600s)
- Domain reload restarts the HTTP service and clears REPL sessions; `--wait`
  follows the matching project and requires this refresh's operation id /
  generation before accepting `ready`

After completion, use `cs doctor` for a full readiness check if needed. To wait
without triggering another refresh, use `cs wait-ready --timeout 120`.

If `refresh --wait` exits 4, do not issue another refresh. Its JSON contains
`expectedRefreshOperationId` and `expectedGeneration`; resume that exact wait:

```bash
cs wait-ready --refresh-operation "<OP_ID>" --generation <N> --timeout 120
```

`operation_in_progress` means the matching refresh is still active.
`outcome_unknown` means the matching target/state could not be confirmed. In
either case inspect `cs doctor`; if the result also includes an HTTP
`invocation.invocationId`, use that UUID with `cs doctor --operation <UUID>` to
diagnose the acceptance request. The refresh operation id and HTTP invocation id
are different identifiers.

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
