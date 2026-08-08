# Unity CLI Custom Catalog

The catalog is a version-controlled shortlist of project-defined commands for the
team. It contains an exact copy of the last verified package custom partition, but
it is not execution authority: always resolve the current Registry Snapshot before
executing a candidate.

## Sync

After registering, removing, or changing project commands, sync from the running
Editor:

```bash
cs catalog sync
```

Sync creates one registry resolver, performs one live fingerprint comparison, and
downloads only a missing or changed partition. It writes only when the custom
fingerprint or contracts changed.

The default file is:

```text
<project-root>/.unity-cli/catalog.json
```

It is deterministic team state: no machine path or timestamp is stored. Writes use
atomic replacement. A malformed response, stale cache, generated fallback, or
write failure returns non-zero and preserves the existing file. Catalog v1 is
intentionally not read; run `catalog sync` to replace it with strict v2.

Use `--catalog-path <path>` to override the target for one invocation.

Report the total plus `added`, `removed`, and `changed` IDs. A verified empty custom
partition is valid and intentionally writes an empty command list.

## List candidates

List the shared shortlist without contacting Unity:

```bash
cs catalog list
```

The text form shows canonical ID, argument names, Editor requirement, and summary.
Use it only to select likely candidates. Then load the exact current contract:

```bash
cs list-commands --offline --view custom --id <canonical-id> --json
```

If offline discovery reports custom commands unavailable, run one live discovery
without `--offline`. Use `cs list-commands --refresh …` only when the user
explicitly asks to force a complete command-list update.

When an exact ID is absent from a first-use fallback, stale cache, or unchecked
offline cache, discovery fails closed with exit code 2 and
`data.kind:"discovery-error"`. The result preserves its registry `source`,
`customAvailable`, `requestedIds`, and any `staleReason`; it does not claim the
ID is unknown until live or live-checked registry evidence proves that absence.
An older cache that cannot satisfy the current routing metadata uses the same
failure envelope and requires live discovery before its command surface is used.
