# Unity CLI Command Execution

Load this execution reference only after discovery has produced one complete
Contract Bundle and only when the task will invoke `command` or `batch`. Planning,
domain closure, exact-ID closure, fallback order, and dry-run behavior live in
`SKILL.md`; do not restart discovery here.

The Unity package owns every executable argument, result, rule, and wire route.
Treat canonical IDs as opaque and follow the selected bundle's `arguments`,
`result`, `rules`, `relations`, and `limitations`. The CLI's routing metadata
never replaces that package-owned contract.

## Request protocol

Write every structured request to the mandatory scratch directory documented in
`SKILL.md`. A single command contains exactly `id` and `args`:

```json
{"id":"gameobject/create","args":{"name":"Wall","primitiveType":"Cube"}}
```

Run it with:

```bash
cs command --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req-create-wall.json"
```

`args` is required and must be an object; use `{}` for a no-argument contract.
Never infer arguments from the ID. Follow the selected Contract Bundle's
`arguments`, `result`, and `rules`, including mutually exclusive selectors and
conditional requirements.

A batch uses the same canonical items:

```json
{
  "commands": [
    {"id":"gameobject/create","args":{"name":"Wall","primitiveType":"Cube"}},
    {"id":"gameobject/get","args":{"path":"Wall"}}
  ],
  "stopOnError": true
}
```

Run it with `cs batch --json --input <file>`. The CLI preflights the complete
batch before sending any item. Batch payloads are static: a later item cannot
reference an earlier item's result. Use caller-defined deterministic paths inside
one batch. When a later command needs a Unity-generated selector, execute the
producer first and construct the dependent payload from its returned field.

## Local preflight

Before HTTP dispatch, the CLI resolves the current package-owned contract and
rejects:

- an unknown or deny-policy ID;
- unknown, missing, empty, or incorrectly cased duplicate arguments;
- invalid scalar, collection, object, enum, or range values;
- violations of package-owned cross-argument rules;
- editor/runtime requirement mismatches;
- session operations without an explicit `--session` ID;
- an empty mutation or ambiguous selector combination.

Built-in and project custom commands use the same preflight. A preflight failure
means Unity did not execute the request; correct the payload instead of retrying it
unchanged.

Execution refuses an unverified stale cache or generated fallback. A successful
command therefore uses either a just-fetched snapshot or a cache whose fingerprint
was compared during that invocation.

## Verification and retry policy

A successful transport response is not by itself proof that the intended Unity
state was reached.

1. For reads, verify returned scope and identifiers against the request.
2. After mutation, follow the Contract Bundle's `relations` and `limitations` and
   perform the smallest independent read-back. Assign each postcondition to one
   proof and remove a read whose full proof purpose is already covered elsewhere;
   retain a narrower read only for a selector or field absent from the broader
   result. Keep a caller-defined deterministic selector only when the consumer
   contract accepts it and the mutation fixed its value. Otherwise, bind the
   read-back only to a returned field that the verifier contract explicitly
   accepts as a selector; a diagnostic field is not a selector merely because
   its name contains `instanceId` or `path`.
3. After asset or C# changes that compile, follow `references/refresh.md`; a
   domain reload clears REPL sessions.
4. If the CLI returns a transport failure after a mutation, state may be
   uncertain. Read back before any manual retry; never blindly invoke create,
   duplicate, destroy, import, or another non-idempotent mutation again.
5. Report completion only when observed state matches the request.

## Project custom commands

`cs catalog list` is a committed team shortlist, not an execution contract. When
it suggests a candidate, load the exact package-owned contract with
`list-commands --view custom --id <canonical-id> --json`. Run `cs catalog sync`
when the shared shortlist needs updating; sync accepts only a registry that was
verified during that invocation.

If no custom command matches, continue to snippets and then raw exec according to
`SKILL.md`.

## Runtime mode

Most authoring commands require the Editor. Session operations and registry
discovery also work against a supported Player service. Use
`--mode runtime --port 15500` only when deliberately targeting a Player build.
