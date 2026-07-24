# Unity CLI Commands

Use the framework command protocol for bounded, structured Unity operations. The
canonical routing order is defined once in `SKILL.md`; this reference covers
domain discovery, request contracts, and result verification.

## Static contract and live schema

`scripts/cli/command_manifest.json` is the canonical static contract for built-in
routing. It records each command's domain, visibility tier, availability, argument
rules, intent boundaries, and verification guidance. The live Unity registry
remains the execution authority and supplies the installed package's schema.

The manifest retains **59** built-in contracts:

- **57 routable contracts** are returned by default discovery.
- `editor/menu.open` and `editor/window.open` are retained for compatibility and
  audit, but are blocked because they require noninteractive UI behavior that
  cannot be verified reliably. Discovery hides them unless `--include-blocked` is
  requested; command preflight rejects their execution.

Do not copy the full registry into agent context. Narrow discovery by domain and
tier, then inspect one exact contract when needed.

## Domain boundaries

| Domain | Use for | Do not use for |
|---|---|---|
| `editor` | Editor state, play mode, console maintenance | Scene contents, object selection, or project asset files |
| `scene` | Scene listing/open/save and hierarchy | GameObject properties or prefab-file contents |
| `objects` | GameObjects, object selection, components, transforms | Asset movement or direct prefab-file editing |
| `assets` | Asset search/import/CRUD, materials, and material assignment | Scene hierarchy or prefab contents |
| `prefabs` | Prefab creation, instantiation, unpacking, direct content editing | Generic asset operations unrelated to prefab contents |
| `capture` | Scene/Game screenshots and Profiler recording | Structured state inspection |
| `control` | REPL sessions and command discovery | Routine Unity authoring |

The agent-facing domain is metadata; it does not rename the wire protocol.
For example, scene and asset operations may still use the `project` namespace.
Always send the exact `ns` and `action` returned by discovery.

## Visibility tiers

- **`core`** — common, bounded, unambiguous operations. Search this tier first.
- **`advanced`** — specialized, destructive, schema-heavy, or lower-frequency
  operations. Search it only after the chosen domain has no core match or the
  user's intent explicitly requires it.
- **`control-plane`** — sessions and command discovery. Do not mix these with
  normal Unity authoring candidates.

Tier changes discovery visibility, not protocol availability. Existing wire ids
remain compatible.

## Progressive discovery

Start with one domain's core commands:

```bash
cs list-commands --offline --domain objects --tier core --json
```

If the requested operation is specialized or absent from core, query only that
domain's advanced commands:

```bash
cs list-commands --offline --domain prefabs --tier advanced --json
```

Inspect one exact canonical id before constructing its request:

```bash
cs list-commands --offline --id prefab/asset_modify_component --json
```

Canonical ids use `<namespace>/<action>`. The returned entry includes the actual
wire namespace, action, committed argument schema, domain, tier, and availability.
Do not guess arguments from an action name.

Useful discovery filters:

```bash
cs list-commands --offline --type builtin --domain scene --tier core --json
cs list-commands --offline --include-blocked --json
cs list-commands --type custom --json
```

`--offline` reads the committed manifest and needs neither a Unity project nor a
running service. Remove it only when checking the current package's live registry
or discovering project-defined custom commands. Use unfiltered,
live, or `--include-blocked` discovery only for maintainer audits. Routine tasks
should expose one domain and tier at a time.

## Request protocol

Write a single JSON object to the mandatory scratch directory documented in
`SKILL.md`:

```json
{"ns":"gameobject","action":"create","args":{"name":"Wall","primitiveType":"Cube"}}
```

Then run:

```bash
cs command --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req-create-wall.json"
```

Omit `args` only for a command whose discovered contract has no arguments. Never
pass structured parameters inline or infer omitted required fields.

The response envelope is:

```json
{"ok":true,"exitCode":0,"summary":"...","data":{}}
```

Check both `ok` and `exitCode`. `data` is already structured; do not reparse a
`resultJson` string unless `--verbose` explicitly returned one.

Common shapes:

- `Vector3`: `{"x":0,"y":1,"z":3}` with all three numeric axes.
- Arrays: JSON arrays of the discovered element type.
- Booleans: JSON `true` / `false`.
- Legacy `active` and `isStatic`: integer `0` / `1` when the discovered schema
  says `int`.
- Object identifiers: provide the meaningful `path` or `instanceId` form required
  by the exact contract; do not send both unless discovery explicitly permits it.

## Local preflight

Recognized built-ins are validated before any HTTP request. Preflight rejects:

- blocked commands;
- unknown arguments;
- missing or empty required arguments;
- wrong scalar, array, vector, or field-pair types;
- invalid enum/range values;
- violations of exactly-one, at-most-one, at-least-one, or conditional argument
  rules;
- session operations that omit an explicit `--session` id.

`batch` preflights every recognized built-in item before sending the batch. A
preflight failure means the rejected command was **not executed**. Fix the request;
do not retry it unchanged.

Project-defined custom commands pass through because their contracts are
project-specific. Use their live or cached schema instead of assuming built-in
validation applies.

## Verification and retry policy

A successful transport response is not by itself proof that the intended Unity
state was reached.

1. For reads, verify that the returned scope and identifiers match the request.
2. After mutation, use the manifest's verification hint and perform the smallest
   independent read-back: `get`, `hierarchy`, `list`, `status`, or a relevant
   screenshot.
3. After asset or C# file changes that require compilation, follow
   `references/refresh.md`; a domain reload clears REPL sessions.
4. If transport fails after a mutation may have been accepted, read back before
   retrying. Never blindly repeat a create, duplicate, destroy, import, or other
   mutation whose execution state is unknown.
5. Report completion only when read-back matches the requested state. Otherwise
   report the observed state and the next recoverable action.

## Custom command fallback

When no built-in contract matches:

1. Run `cs catalog list` to inspect the committed per-project custom-command cache.
2. If it is missing or stale, run `cs list-commands --type custom --json`.
3. Run `cs catalog sync` only when the cache needs to be refreshed.
4. Return to the fallback order in `SKILL.md` if no custom command matches.

See `references/catalog.md` for catalog maintenance. Do not read or edit the
catalog as a substitute for its CLI workflow.

## Runtime mode

Most built-ins are Editor-only. Session operations and command discovery also work
against a supported Player service. Use `--mode runtime --port 15500` only when
deliberately targeting a Player build.
