# Unity Test Runs

Read this reference only after routing a request to the `tests` domain. The
committed contracts require the package capability `test_runs_v1`; offline
discovery alone does not prove that the installed package supports them. Verify
the live `tests` registry before the first run.

## Start one run

`tests/run` starts one asynchronous run in the open Unity 2022 Editor. `mode` is
required and is exactly `"edit"` or `"play"`. Omit `testNames` to run every
test in that mode. If supplied, `testNames` must contain 1–32 nonempty exact
names, each at most 512 UTF-16 code units.

Start it with a direct `cs command` request. `tests/run` is not allowed inside
`cs batch` because its protected invocation id is the durable identity of the
asynchronous run.

Save the returned 32-character hexadecimal `runId`. It identifies the Unity Test
run. The package derives it from the protected HTTP invocation UUID by removing
the hyphens, so an `outcome_unknown` response can still be recovered from
`invocation.invocationId` without repeating `tests/run`.

## Inspect current or historical state

Call `tests/status` with that `runId`. It can inspect an active run or a retained
historical run; retention is bounded and controlled by the package. If the
requested run is no longer retained, status fails as not found rather than
returning another run.

For normal polling, set `waitSeconds` to `10` so one call can wait briefly for
progress instead of repeatedly returning unchanged state. Report success only
when the returned phase is terminal and `outcome` is `passed`. The compact
result also includes flat summary counts, the current test, result state,
duration in seconds, lifecycle timestamps, a message, bounded failure details,
and `failuresTruncated`; when that flag is true, failure evidence is incomplete.

If a Unity domain reload disconnects the request, run `cs wait-ready`, then call
`tests/status` again with the same `runId`. Never call `tests/run` again to poll,
resume, or recover an existing run.
