# Unity CLI Test Runs

Run Unity Test Framework tests (EditMode or PlayMode) and wait for structured
results. Use after changing project code that the project's test suite covers,
or when the user asks to run Unity tests.

**Recommended (one-step):**

```bash
cs test                      # EditMode, waits up to 300s
cs test playmode             # PlayMode, enters/exits play mode automatically
cs test --filter Namespace.Fixture.TestName --wait 120
cs test --group "MyGame\.Combat\..*"
```

- The positional mode is `editmode` (default) or `playmode`
- `--filter NAME` runs one full test name; repeat the flag for several tests
- `--group REGEX` selects fixtures/namespaces by regex; repeatable
- `--wait TIMEOUT` bounds the wait (default 300s, max 600s); `--wait 0` starts
  the run and returns immediately with the runId
- `--force` supersedes a stale in-progress record (only after a previous run
  was interrupted; never to run two suites concurrently)

Exit codes: `0` all tests passed · `3` failures, aborted run, or rejected start
(including missing test framework) · `4` no completion within the wait budget
(poll `editor/test.status` later) · `1` project/transport errors.

Failure output lists each failed test name with the first line of its message.
Use `--json` for the full structured report (counts, duration, failures).

**Behavior notes:**

- The run is asynchronous in the editor; `cs test` polls the
  `editor/test.status` command and tolerates the service dropping during
  PlayMode transitions and domain reloads.
- PlayMode runs enter and exit play mode on their own. Do not wrap them in
  `editor/playmode.enter`/`exit`, and expect REPL sessions to be cleared by the
  reloads.
- Only the latest run is tracked; starting a new run overwrites the previous
  result. Runs started from the Unity Test Runner window are also visible to
  `editor/test.status`.
- `editor/test.run`/`editor/test.status` require `com.unity.test-framework` in
  the project. Without it they stay discoverable but return an explanatory
  error; report that to the user instead of retrying.
- If the editor exits mid-run — crash or quit — the record is reported as
  `aborted` on the next start.

For structured routing, the same capability is exposed as the canonical
commands `editor/test.run` and `editor/test.status`; `cs test` is the
convenience wrapper that binds start, poll, and report into one invocation.
