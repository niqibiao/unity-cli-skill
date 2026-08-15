# Unity CLI Refresh

Trigger Unity to re-scan assets and recompile scripts. Use after writing or
modifying `.cs` files on disk.

**Recommended (one-step):**

```bash
cs refresh --wait 120
```

- `--wait TIMEOUT` blocks until the refresh + compile + domain-reload cycle
  completes (bare `--wait` defaults to 60s, max 600s)
- The wait is bound to this refresh operation (id + generation), so a stale
  ready mirror or a previous generation's compile failure cannot end it early
- Domain reload restarts the HTTP service and clears REPL sessions; `--wait`
  handles reconnection
- A compile error surfaces as `editor.compile_failed` attributed to this
  operation instead of a timeout

Do not add `--exit-playmode` by default. Exiting play mode discards the user's
runtime state, so it needs their explicit approval.

**If the wait fails with `editor.play_mode_deferring_compile`:** the editor is
in play mode and the user's Script Changes While Playing preference defers
compilation until edit mode. The command fails fast instead of burning the
timeout. Ask the user whether to exit play mode (state how running state is
lost). Only after they approve:

```bash
cs refresh --exit-playmode --wait 120
```

(or exit play mode first with the `editor/playmode.exit` command, then rerun
the plain refresh). If they decline, leave play mode running; the compile
stays deferred until they exit play mode themselves. With the other two
preference values Unity resolves play mode on its own, so the plain `--wait`
keeps waiting and completes without this finding.

**Diagnosis without mutation:**

```bash
cs doctor            # one-shot findings: project, package, versions, service
cs wait-ready --timeout 60   # poll until ready; add --expect-operation/--min-generation
```

Both work before the package is installed and never trigger a refresh.

After completion, verify with `cs status` if needed.
