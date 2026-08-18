---
name: unity-cli
description: >
  Drive a live Unity Editor or Player with unity-cli. Use when a task must inspect
  or change Unity scenes, GameObjects, components, transforms, prefabs, materials,
  ScriptableObjects, project assets, play mode, screenshots, profiler recording,
  Unity Test Framework runs, or execute C# inside Unity; also use for Unity console
  maintenance and unity-cli setup, status, refresh, snippets, or custom commands.
  Do not use for source-only Unity coding that does not require interaction with
  the live Editor or Player.
---

# Unity CLI

One CLI (`cs`) drives the Unity Editor/Player. The default authoring surface is
grouped into six agent-facing domains so only the relevant schema enters context;
control-plane and project custom commands use explicit views.
`cs list-commands` is a control-plane CLI discovery operation, not the canonical
package contract `command/list`. Its CLI flags are never `command/list` arguments,
and discovery invocations must not be represented as canonical command routes.
Only use `command/list` when the user explicitly requests that package contract.
The records returned by discovery carry authoring, custom, or control-plane tiers.

## Running `cs`

`cs` below means:

```bash
python "<SKILL_DIR>/scripts/cli/cs.py"
```

`<SKILL_DIR>` is this skill's absolute base directory, supplied when the skill
loads. Expand `cs` to that absolute command on every call and run it without
changing directory.

Do not enumerate other skill installations or run `cs --help` to rediscover this
documented surface; this file and its returned Route Cards are complete for
routing.

Do not pass `--project` during normal use. The CLI locates the Unity project by
walking up from both the working directory and its own committed location.
`--project <path>` is only for deliberately targeting a different project.

The CLI runs in place; there is no bootstrap or copy step. `cs setup` is a
convenience, not a gate. If the package is absent or `--update` is used, setup
writes the shared `Packages/manifest.json`. Before any write-producing setup,
state the exact source it will add and obtain the user's approval. See
`references/setup.md`.

## Passing inputs

Never pass C# code or structured command parameters inline through the shell.
Write a file, then pass its absolute path:

- Raw C# for `exec` goes in a `.cs` file via `--file`.
- Structured parameters for `command` and `batch` go in a JSON file via
  `--input`.

The mandatory scratch directory is:

```text
<project-root>/Temp/CSharpConsole/AgentScratch/
```

This is inside Unity's managed `Temp/`, is not imported, and is normally ignored
by git. Always use the full absolute path. Name files for the semantic task and
overwrite the same file when revising it serially; use a random suffix only when
another agent is known to work on the same Unity project concurrently.

Scratch payloads are one-shot. Put reusable C# in the snippet library. Clean only
`AgentScratch/`; never delete `Temp/CSharpConsole/`, which contains service state.
Never write REPL payloads under `Assets/`: importing them can break project
compilation and prevent the service from restarting.

```bash
cs exec --file "<project-root>/Temp/CSharpConsole/AgentScratch/inspect-camera.cs"
cs command --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req-create-cube.json"
cs batch --json --input "<project-root>/Temp/CSharpConsole/AgentScratch/req-batch.json"
```

## REPL sessions

`cs exec` starts a fresh session by default. Keep that default for self-contained
code. When later submissions intentionally depend on variables, `using`s, types,
or helpers created earlier, generate one opaque task-specific id and reuse it:

```bash
cs exec --session agent-a1b2c3 --file "<project-root>/Temp/CSharpConsole/AgentScratch/inspect-camera.cs"
cs exec --session agent-a1b2c3 --file "<project-root>/Temp/CSharpConsole/AgentScratch/use-camera.cs"
```

Never share a session between unrelated tasks or agents. A domain reload clears
session state, so after `cs refresh` rebuild the context or use a new id. See
`references/exec-code.md` for lifecycle and reset details.

## Routing

Use this canonical order for every task: **built-in command → project custom
command → snippet → raw exec**. Do not skip a matching built-in in favor of C#.
The package registry is the executable contract authority; the committed custom
catalog is only a shared shortlist.

For a structured Unity operation, finish discovery before loading execution
details:

The routine built-in authoring discovery budget is one Route Card call per
non-empty tier, then one exact-ID call total. Only a genuinely new requirement
after closure permits one narrow built-in lookup, with its reason recorded.
Keep a discovery ledger. Scope that ledger to the current agent session and
resolved project target. Its key includes selector kind (`domain` or `id`),
normalized view, tier, sorted selectors, and resolution mode (`live`, `offline`,
or explicit `refresh`), plus projection/detail mode (`compact` or `verbose`).
Mark a signature in flight before launch and completed after success; a completed
or in-flight signature must be reused or awaited. Never use an offline entry to
satisfy the required live comparison. A stored live or refresh entry may satisfy
the otherwise identical offline request only for the same project, ledger
generation, and detail mode after `cacheStored:true`; no other cross-mode or
cross-detail reuse is valid. The ledger is in-memory agent bookkeeping; do not
query wall-clock time or create a ledger file.
Refresh is an exclusive barrier: wait for every in-flight lookup for that project,
then launch refresh alone. Only a refresh result with `source:"live"` and
`cacheStored:true` creates a new ledger generation; record that refresh as its
first entry and invalidate the older completed entries. A stale-cache or fallback
refresh never advances the generation or satisfies a later offline lookup; discard
any late result from an older ledger generation instead of recording or using it.
Do not retry a live or refresh lookup after timeout or an unknown terminal state;
stop and report its state as uncertain. Only an offline lookup with a confirmed
non-zero exit and no result may be retried once, under the same ledger entry and
with its reason recorded. A successful lookup is never resent.

1. **Close domains before calling.** Build the complete Core and Advanced domain
   sets from every requested mutation, prerequisite, explicit invariant,
   independent proof, and fallback check. The table below is the routine Domain
   Index; skip an unfiltered index call. The Advanced-only list is conditional,
   not a checklist: direct `prefab/asset_*` editing, `prefab/unpack`,
   asset-folder creation, and Scene View capture are Advanced. `prefab/create`
   and `prefab/instantiate` stay in `prefabs/core`; generic prefab enumeration
   stays in `assets/core`. The same domain may belong to both tiers. A folder plus
   material workflow puts `assets` in both sets: folder creation is Advanced,
   while material creation, inspection, and assignment are Core. Never let an
   Advanced intent replace another requested Core operation in that domain. When
   prefab-domain intents exist, omit `prefabs/core` only if all of them are direct
   `prefab/asset_*` operations or `prefab/unpack`.
   Every domain/tier selection must map to an affirmative requested operation;
   exclusions, examples, and parallelism never widen the query set. An explicitly
   named read or proof selects its tier, while a generic read-back for a mutation
   does not because the exact bundle supplies its direct relation. A possible
   later drill-down never widens the initial domain sets; wait until an upstream
   read makes it necessary. A domain whose only possible use is such a deferred
   drill-down is empty and must be omitted. For example, a hierarchy-wide scene
   scan does not select `objects` merely because one returned object might later
   need inspection; the scan/report intent is the hierarchy read, not an implicit
   per-object or per-component read. For a scan of every prefab-asset hierarchy,
   enumeration is `assets/core` and hierarchy inspection is `prefabs/advanced`;
   omit `prefabs/core`.
2. **Run the Route Card round.** Use exactly one multi-domain query for each
   non-empty tier. The compact result retains `cacheStored` because it guards
   generation continuity. The first live call performs the session's one
   fingerprint comparison. When both tiers are non-empty in a live workflow, Core
   is the first live call; Advanced may be first only when Core is empty. Finish
   that live call before any later offline tier call. If it reports
   `source:"live"` with `cacheStored:false`, stop and report the persistence
   failure instead of mixing registry generations. A generated fallback may
   continue planning-only. In an explicit offline/no-project dry-run, add
   `--offline` immediately and, when both tiers are non-empty, launch the Core and
   Advanced queries together in one tool round. Do not probe `status`, the
   filesystem, or the working directory to reconfirm supplied context. If Core
   alone reveals that a requested operation needs Advanced, make the one Advanced
   call for all such unresolved operations. A stale cache or generated fallback
   is planning-only; execution requires a registry verified live during that
   invocation.
3. **Run the exact round once.** Wait for every Route Card call to finish before
   building the one exact-ID command. Take the union of IDs selected from every
   tier and branch, closing it over every mutation, producer, consumer, loop
   branch, invariant, and proof; copy IDs only from returned cards.
   Issue one multi-ID exact query in exactly one shell invocation. The domain-query
   parallelism rule never applies to exact discovery: do not split the union into
   parallel or later calls, and do not add a later exact lookup for any original
   task requirement. Do not exact-load a conditional drill-down whose necessity
   depends on a future read. Omit deferred drill-downs from both the exact selector
   set and the invocation plan; record only their trigger in prose until it fires.
   Invocation-plan routes are executable in the current phase, so never add a
   deferred route with an "if" or "when" purpose. In a broad scan, a per-item
   getter whose contract does not name the required diagnostic signal cannot close
   that gap; record the capability gap instead of selecting the getter.
   Runtime selector binding alone does not make a known consumer conditional;
   include its contract in the original closure. After a read justifies genuinely
   new drill-down IDs, combine them into at most one narrow follow-up lookup.
   Treat `selected`, `related`, and `denied` as one closed result: `selected` and
   `related` are complete executable contracts, while `denied` entries are
   non-executable policy records, not contracts. Remove every returned ID from the
   unresolved set and never query one merely to promote it to `selected`. `related`
   is the complete direct, deduplicated prepare/read-back layer, not a recursive
   expansion. A denied requested intent is terminal: stop as blocked and do not
   enter fallback. Contract evidence is closed-world for task-specific signals: a
   generic result container or summary does not promise an unnamed marker or
   field. If the executable bundle lacks a required capability or proof, or a
   limitation blocks one, record a capability gap instead of probing for a
   stronger built-in. Selected and related contract entries are capabilities, not
   mandatory invocations; use one only for a requested mutation, prerequisite, or
   independent proof.
4. **Plan concrete invocations.** Assign each postcondition to the smallest
   independent proof and remove fully redundant reads. Deduplicate contracts, not
   invocations. An explicit verifier limitation is authoritative; another generic
   read closes it only when that read's own contract names the required
   postcondition, otherwise record a proof gap. Do not collapse materially
   different argument values or user-named selectors into prose: retain one
   template for each distinct argument branch and one invocation for each explicit
   read-back target; a uniform generated series may remain one counted loop. A
   counted uniform series uses its first generated item as the concrete template.
   When a producer can create an object under its requested known parent, create
   it at that parent directly; do not add a later reparent mutation. When argument
   behavior branches by a predicate such as parity, use the first member of every
   branch as its template; do not substitute arbitrary later samples. One contract
   supports all of them without more discovery. Do not add speculative existence,
   conflict, or overwrite prechecks unless the task or selected contract requires
   them. A default may satisfy an exact invariant without making the invariant
   optional:
   a newly created Cube already has exactly one `BoxCollider`; use it instead of
   adding another, but retain “exactly one” as an explicit requirement and proof
   obligation. Never use a current or default value to drop an explicitly
   requested assignment: for example, still plan `component/modify` when the user
   asks to set that existing collider's `m_IsTrigger`, even when its default
   already matches.
5. **Separate planning from execution.** A dry-run still plans every supported
   requested mutation; state separately that nothing was executed. Static batches
   may use caller-defined deterministic paths. When Unity chooses an identity,
   execute the producer separately and bind consumers to its returned selector
   instead of predicting a name. Discover a repair only when it may execute;
   missing rules keep diagnosis read-only and exclude repair commands from domain
   selection, exact lookup, and the invocation plan. A transport failure after
   mutation leaves state uncertain: read back first and never blindly repeat a
   non-idempotent create, duplicate, destroy, import, or equivalent mutation.

```bash
cs list-commands [--offline] --domain scene --domain objects --tier core --json
cs list-commands --offline --id gameobject/create --id gameobject/get --json
```

In a returned contract, `arguments: []` means there are no named parameters; do
not invent an argument named `args`. Only an actual `command` request uses the
outer `"args": {}` wrapper.

Do not mix `--domain` with `--id`; use `--tier` only with domains. Preserve
`--view control` or `--view custom` when exact-loading an ID from that view;
authoring IDs use the default view.

For planning or discovery only, do not load `references/commands.md`; these
routing rules plus the returned Route Cards and Contract Bundle are complete.
Before invoking `command` or `batch`, read `references/commands.md` completely
once for the request, preflight, and retry protocol. Then write exactly
`{"id":"<canonical-id>","args":{...}}` to the mandatory scratch JSON file and run
`cs command --json --input <file>`; `args` is required even when empty.

Discover contracts only through `list-commands`; never read or search
`scripts/cli`, generated registry data, routing metadata, or Python
implementation files to recover command details. Those files are
non-agent-facing implementation data and may be much larger than the selected
contract bundle.

Enter fallback only after the complete built-in plan has a concrete gap. Run
`cs catalog list`, load a suggested exact custom contract, then try
`cs snippets search "<intent>"`, and finally raw `exec`. Do not return to
built-in discovery for a requirement knowable before fallback; a genuinely new
requirement permits one narrow exact lookup with the reason recorded. If no Unity
project is available, mark custom and snippet stages unavailable without probing
them. An unavailable fallback stage is not selected; do not load its reference.
Merely describing the fallback order does not select a stage. Load its reference
only immediately before performing that stage's documented operation. The
selected custom-contract lookup is the fallback-stage exception to the built-in
one-exact budget. A dry-run that stops before authoring raw C# records the `exec`
decision without reading `references/exec-code.md`.

Use `cs list-commands --refresh …` only when the user explicitly asks to update
the command list. Do not load or print the unfiltered 61-command package registry
during routine work.

| Domain | Positive intent | Exclude / route elsewhere |
|---|---|---|
| `editor` | Editor state, play mode, test runs, console maintenance | Scene contents, object selection, asset files |
| `scene` | Scene listing/open/save and hierarchy-wide reads, including component summaries | Properties of an identified GameObject/component, prefab contents |
| `objects` | Operations on an explicitly identified scene GameObject, component, transform, or selection | Hierarchy-wide scans, unknown future targets, project assets, prefab-file editing |
| `assets` | Asset search/import/CRUD, materials, and ScriptableObjects | Scene hierarchy, prefab contents |
| `prefabs` | Create/instantiate/unpack or directly inspect/edit prefab contents | Generic prefab enumeration (`assets/core`) and unrelated asset paths |
| `capture` | Scene/Game screenshots and Profiler recording | Structured state inspection |

The five control-plane contracts are outside the default authoring view. Inspect
them only when the user's requested capability is itself control-plane, using
`--view control --domain control --tier control-plane`. Merely using
`cs list-commands` for discovery does not justify querying the control view or
loading or planning the `command/list` contract.

`editor/menu.open` and `editor/window.open` are deny-policy intents, not
executable contracts. An explicit `list-commands --id` lookup reports them under
`denied` with `invoke=false`; classify that result as blocked and stop. Never
route those intents to snippets or raw `exec` as an automatic fallback.

### Other subcommands

| Task | Subcommand | Detail |
|---|---|---|
| Raw C# fallback | `cs exec --file` | `references/exec-code.md` |
| Reusable C# | `cs snippets …` | `references/snippets.md` |
| Snippet audit | `cs snippets doctor` / `stats` | `references/snippets-audit.md` |
| Refresh and compile | `cs refresh` | `references/refresh.md` |
| Readiness diagnosis / wait | `cs doctor` / `cs wait-ready` | `references/refresh.md` |
| Unity Test Framework runs | `cs test` | `references/tests.md` |
| Editor log inspection | `editor/console.mark` + local read | `references/logs.md` |
| Custom-command catalog | `cs catalog sync` / `list` | `references/catalog.md` |
| Package / connection state | `cs status` / `cs health` | `references/status.md` |
| Package setup | `cs setup` | `references/setup.md` |

## Output conventions

- Use `--json` on `command`, `list-commands`, and `batch`. In the default compact
  form, check `ok` and the CLI process exit status. Add `--verbose` only for
  diagnostics; its full envelope also exposes `exitCode`, which must agree with
  the process status.
- Other subcommands print a cheaper text form. Add `--json` to `exec` only when
  its structured envelope is specifically needed.
- A version-mismatch warning means the installed package and CLI use different
  `major.minor` lines. Follow `references/setup.md`; the warning does not itself
  block execution.
- Expanded CLI commands and JSON payloads are agent-internal. When Unity must be
  opened or focused, tell the user what action is needed in plain language, then
  run `cs status` yourself to verify the result.
