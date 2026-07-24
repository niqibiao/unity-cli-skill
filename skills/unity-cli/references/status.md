# Unity CLI Status

Use the smallest Unity/project-read-only check that matches the question.
`doctor` and `wait-ready` may create and delete a probe in the machine-local
outbox cache to verify that future protected invocations can be recorded:

- `cs status` — concise project, package, connection, protocol, and Unity 2022
  compatibility status.
- `cs health` — raw live service snapshot.
- `cs doctor` — actionable project identity, protocol, journal, compile, and
  readiness diagnosis.
- `cs wait-ready --timeout 120` — wait without installing, refreshing, exiting
  Play Mode, or otherwise mutating Unity. Add `--refresh-operation <OP_ID>
  --generation <N>` when resuming a timed-out refresh wait.
- `cs doctor --operation <UUID> --json` — reconcile an uncertain operation with
  the local outbox and Unity's durable receipt.

```bash
cs status
cs doctor
cs wait-ready --timeout 120
cs wait-ready --refresh-operation "<OP_ID>" --generation 7 --timeout 120
```

Reports:
- **project**: Unity project root path
- **package**: whether `com.zh1zh1.csharpconsole` is installed and resolvable
- **service**: whether the Unity HTTP service is reachable at the configured port
- **version**: package/protocol versions and Unity 2022 compatibility, or the
  on-disk package version when the service is down

Exit code 0 means **fully operational** (service reachable and healthy); any
degraded state — no project, package missing, service unreachable — exits 1.
Read the text to see which layer is down.

`doctor` and `wait-ready` additionally verify that the reachable service belongs
to this project and supports the protocol-v2 at-most-once journal. They describe
the compatible editor as **Unity 2022**; exact patch information appears only in
verbose raw evidence.

`wait-ready` follows a matching service across domain reload and local port
changes. Compile failure, wrong-project identity, incompatible protocol, or an
unwritable journal fail immediately. A normal reload or temporarily refused
connection remains waitable until the monotonic deadline. A bound refresh timeout
returns exit 4: `operation_in_progress` when the same op is visibly active, or
`outcome_unknown` when the target/op cannot be confirmed. Do not start a new
refresh; resume the same op/generation.

## Uncertain operations

Every protocol-v2 HTTP invocation is bound to one UUID and exact request bytes. Unity
durably claims it before dispatch and persists the response before writing the
socket. Within the advertised dedupe window, a retry with the same UUID therefore
replays or reports the existing state instead of dispatching twice. The CLI uses
that retry only inside the original bounded request. Once its local outbox records
the id as sent, a later CLI process will not dispatch it again—even after Unity's
retention window expires—and directs recovery through `doctor`.

If an HTTP invocation result is `outcome_unknown` or
`operation_in_progress` (exit 4), do not replace it with a new invocation id:

```bash
cs doctor --operation "<UUID>" --json
```

Then perform the smallest independent read-back. `completed` means the handler
returned and its response is replayable; it does not replace verification that
the requested Unity state was reached. `operation_in_progress` means to keep
observing the same UUID. `outcome_unknown` means it may have run and will not be
dispatched again. `protection_expired` means a local completed receipt remains but
Unity no longer retains the server record; this CLI still refuses to reuse the id.
Refresh lifecycle `opId` / `generation` values are separate from that HTTP
invocation UUID; recover them with the bound `wait-ready` form above.

**Version mismatch handling:** if the output contains `⚠` indicating CLI/package
version misalignment, do NOT just report the mismatch. Explain that the installed Unity
package and the bundled CLI are on different `major.minor` lines, and ask the user to
align the package — update it in Unity (Package Manager, or bump the git tag / version
in their project) to match the CLI, then re-run `cs status` to confirm.

**Reporting:** report only what `status` returned. When suggesting next steps, do
NOT invent CLI subcommands — raw C# is `cs exec`, there is no `cs run`. If unsure
which subcommands exist, check `cs --help` — not `cs list-commands`, which lists
Unity framework commands and needs the editor service running.
