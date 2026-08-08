# Package-owned command discovery

Status: implementation in progress

This specification follows the ownership decision in
`docs/adr/0001-package-owned-command-registry.md` and uses the vocabulary defined in
`CONTEXT.md`.

## Problem Statement

The current command-routing branch improves Command Routing Accuracy, but it does so
by committing a second, hand-maintained copy of the package's command contracts in
the CLI. A package contract change therefore requires synchronized edits in two
repositories, and a missed edit can make discovery or local preflight disagree with
what Unity actually executes.

The current discovery shape also makes an agent load complete domain schemas before
selecting commands and then load much of the same information again for exact
commands. In the measured complex workflows this preserved expectation completion
but increased input tokens by 56.1%, tool output by 138.6%, and reduced the first
cross-domain workflow from 100% to 25%. This consumes context without improving
Effective Completion Rate.

Built-in commands, custom project commands, authoring guidance, and control-plane
operations are currently mixed in one live listing. Redundant, prohibited, and
low-frequency commands are too easy to select, while the distinction between the
package's executable registry and the CLI's Agent Command Surface is unclear.

The user needs command discovery that remains accurate when the Unity package
changes, works offline after an initial snapshot is available, presents only the
information required at each routing stage, and avoids repeated discovery during
complex workflows.

## Solution

Make `com.zh1zh1.csharpconsole` the sole owner of executable built-in and custom
Canonical Command Contracts. The package exposes deterministic, versioned Registry
Snapshots whose registry generation token is derived from SHA-256 Registry
Fingerprints of the normalized built-in and custom partitions. There is no bundled
offline artifact; the machine-local Command Cache initialized by the first
successful live resolution is the only offline source.

The CLI owns a small Routing Overlay, a per-project machine-local Command Cache, and
agent-facing projections. At the start of a live discovery cycle it performs one
conditional snapshot request carrying the cached registry generation token. The
package answers "unchanged" when the token equals its current generation and returns
the full current Registry Snapshot otherwise; the CLI treats the token as opaque. A
user-requested refresh omits the token and always fetches the full snapshot.
Subsequent discovery in the same agent workflow uses the resolved cache.

Discovery becomes progressive:

1. The skill presents a short Domain Index.
2. One repeatable multi-domain query returns a deduplicated Discovery Set containing
   Route Cards only.
3. One repeatable multi-id query returns a Contract Bundle containing an
   execution-complete agent projection of the selected Canonical Command Contracts
   and one deduplicated layer of related preparation and verification contracts.
   The default projection keeps arguments, requirements, preflight rules, and a
   top-level result inventory; `--verbose` exposes the full normalized package
   contract only for diagnostics.
4. Built-in execution accepts only a Canonical Command ID and structured arguments.
   The CLI translates that identity into the package's existing wire routing.

The Agent Command Surface contains 51 authoring commands after removing prohibited
menu/window commands, merging two redundant reads, keeping control-plane commands out
of authoring discovery, and moving two specialized operations to the Advanced tier.

## User Stories

1. As a Unity automation user, I want the commands shown to my agent to match the
   installed package, so that it does not construct requests from stale contracts.
2. As a Unity automation user, I want package upgrades to refresh command metadata
   automatically, so that I do not maintain equivalent schemas in two repositories.
3. As a Unity automation user, I want unchanged registries to use a local cache, so
   that routine discovery does not repeatedly transfer the full command list.
4. As a Unity automation user, I want an explicit refresh option, so that I can force
   a new snapshot after changing project-defined commands.
5. As a Unity automation user, I want built-in and custom command changes detected
   independently, so that adding a project command does not invalidate stable
   built-in metadata.
6. As a Unity automation user, I want offline discovery to use my last valid cache,
   so that planning still works while Unity is unavailable.
7. As a first-time user without a cache, I want resolution to fail with explicit
   guidance to start the editor service once, so that the agent never plans against
   guessed contracts. (Supersedes the earlier first-use generated-snapshot story;
   the offline artifact is removed.)
8. As a user whose editor is unreachable, I want stale-cache results labeled as
   unavailable for verification rather than guessed, so that the agent does not
   invent project-specific contracts.
9. As an agent, I want a short Domain Index, so that I can identify relevant Unity
   capabilities without loading every command.
10. As an agent, I want to request several domains in one operation, so that
    cross-domain work does not require repeated registry calls.
11. As an agent, I want domain discovery to return Route Cards only, so that I can
    select commands without consuming argument and result schemas prematurely.
12. As an agent, I want Route Cards deduplicated across requested domains, so that
    the same routing candidate never appears twice.
13. As an agent, I want to request several exact command ids in one operation, so
    that I can prepare a multi-command workflow with one contract lookup.
14. As an agent, I want an exact query to return execution-complete Canonical
    Command Contracts, so that I can construct valid requests without another
    lookup while avoiding unused package and nested-result diagnostics.
15. As an agent, I want preparation and verification relations included with selected
    contracts, so that I can complete and validate common mutations.
16. As an agent, I want related contracts expanded only one layer, so that discovery
    cannot recursively pull an entire command graph into context.
17. As an agent, I want related contracts deduplicated, so that shared verification
    commands appear once.
18. As an agent, I want limitations to remain attached to the selected contract, so
    that I do not mistake a relation for a guarantee.
19. As an agent, I want one Canonical Command ID shared by discovery and execution,
    so that I do not translate between competing agent-facing identities.
20. As an agent, I want the CLI to translate a Canonical Command ID to the wire route,
    so that package transport details do not leak into prompts.
21. As an agent, I want unknown built-in ids rejected before dispatch, so that routing
    mistakes cannot mutate Unity.
22. As an agent, I want invalid arguments rejected against the package-owned
    contract, so that preflight and execution use the same schema.
23. As an agent, I want custom command contracts passed through from the live or
    cached registry, so that project extensions do not need CLI releases.
24. As an agent, I want control-plane commands excluded from authoring discovery, so
    that session/catalog mechanics do not compete with Unity authoring commands.
25. As a safety-conscious user, I want menu and arbitrary window-opening intents
    explicitly denied, so that the agent cannot substitute a risky fallback after
    their executable commands are removed.
26. As an agent checking editor state, I want play-mode transition information in
    `editor/status`, so that I do not need a redundant status command.
27. As an agent inspecting a GameObject, I want transform data available through
    `gameobject/get`, so that I do not need a redundant transform read.
28. As an agent importing assets, I want import and reimport to remain distinct, so
    that their different intent and side effects remain explicit.
29. As an agent doing routine work, I want only common commands in the Core tier, so
    that specialized commands do not crowd default discovery.
30. As an agent that specifically needs folder creation or a Scene View screenshot,
    I want those commands available in the Advanced tier, so that they remain usable
    without appearing in routine routing.
31. As a maintainer, I want Registry Fingerprints based on normalized contract data
    rather than package versions, timestamps, or registration order, so that equality
    reflects executable schema equality.
32. As a maintainer, I want deterministic generated snapshots, so that a package
    rebuild without contract changes produces identical fingerprints.
33. As a maintainer, I want fingerprints represented as SHA-256 values, so that the
    cache protocol has one explicit and collision-resistant identity.
34. As a maintainer, I want malformed or partially written caches rejected, so that
    discovery never treats corrupted metadata as authoritative.
35. As a maintainer, I want cache files stored per project outside the project tree,
    so that machine-local state never appears in source control.
36. As a maintainer, I want the original routing and live-contract cases to keep
    passing, so that the redesign preserves demonstrated correctness.
37. As a maintainer, I want complex workflow evaluations to record the actual model,
    reasoning level, CLI revision, and traces, so that performance evidence is
    reproducible.
38. As a maintainer, I want graders to derive verdicts from new outputs, so that stale
    hard-coded results cannot make a changed implementation appear successful.
39. As a maintainer, I want test, evaluation, and generated report files to remain
    local, so that implementation pull requests contain only product changes and
    durable design documentation.
40. As a downstream contributor, I want this registry redesign delivered before
    later reliability work is replayed, so that later pull requests rebase onto one
    stable command contract.

## Implementation Decisions

- The package is the sole source of truth for executable built-in and custom
  Canonical Command Contracts. The CLI must not contain a hand-authored duplicate
  of argument, result, validation, or execution schemas.
- The package's registration metadata must be rich enough to produce the executable
  contract used for both discovery and CLI preflight. Contract generation is part of
  registration, not a separate manually synchronized catalog.
- A Registry Snapshot has an explicit schema version and contains a deterministic
  normalized representation of its contracts. Snapshot compatibility is governed by
  that schema version rather than by a package version string.
- Each contract carries one Canonical Command ID, its internal wire namespace/action
  mapping, command partition, availability requirements, argument contract, result
  contract where declared, and execution constraints needed by preflight.
- Built-in and custom contracts are normalized and fingerprinted as separate
  partitions. A Registry Fingerprint is a lowercase 64-character SHA-256 hex digest.
- Registry partition is determined by registration provenance: package handlers are
  built-in and commands auto-discovered from project assemblies are custom. Extension
  authors do not select or impersonate a registry partition through the command
  attribute; compatibility with the previous selector is not required.
- Canonical bytes use UTF-8, invariant value formatting, fixed field semantics, and
  ordinal command ordering by Canonical Command ID. Argument order follows the
  declared executable contract. Incidental reflection, dictionary, assembly-load, or
  JSON serializer order must not affect the digest.
- Fingerprints are computed from normalized contract data only. They exclude
  timestamps, cache metadata, package paths, transport envelopes, and the fingerprint
  fields themselves.
- The package exposes one snapshot operation that accepts an optional caller-supplied
  registry generation token. When the token equals the current registry generation
  the response reports "unchanged" and returns no contracts; otherwise the response
  returns the full normalized built-in and custom partitions, their fingerprints,
  and the current registry generation in one internally consistent payload.
- The CLI treats the registry generation as an opaque token and never recomputes
  fingerprints at runtime. Cross-implementation serializer agreement is enforced on
  the live seam: the parity check fetches the current snapshot and proves the
  canonical writer reproduces its fingerprints and generation.
- Drift defense is scoped to contract data. Command ids, argument schemas, and
  descriptions are authored once in the package and consumed verbatim by the CLI,
  and the live parity check proves both serializers agree on that data. Divergence
  in evaluator implementation semantics between CLI preflight and the package
  binder carries no dedicated gate; the binder always runs and is authoritative,
  so any disagreement resolves to the binder's verdict at dispatch time.
- No generated offline snapshot artifact exists in either repository. First use
  requires one reachable editor service to initialize the machine-local Command
  Cache; every later offline scenario is served from that cache.
- Runtime custom discovery must invalidate or advance the registry generation when
  the effective custom contract set changes. A snapshot must not silently retain a
  pre-discovery custom partition.
- The executable registry and Agent Command Surface are separate concepts. Package
  snapshots may contain control-plane contracts; the CLI projection decides whether
  they are visible for authoring.
- The CLI stores the latest valid snapshot and its registry generation token in a
  per-project Command Cache under the operating system's user cache.
- Cache replacement is atomic. Schema-version mismatch, malformed content, or an
  interrupted write makes that candidate unusable without destroying the last valid
  cache; a stored cache that fails to parse is discarded and resolved live again.
- A normal live registry resolution performs exactly one conditional snapshot
  request. An "unchanged" answer reuses the cached snapshot; any other answer
  replaces the cache with the returned full snapshot. Partition-level differential
  fetching is not performed. A user-requested refresh fetches the full current
  snapshot even when the token would compare equal.
- The agent operating flow performs one live comparison at the beginning of a
  session's discovery and uses the resolved cache for later domain and exact queries.
  It does not repeatedly refresh between those queries unless the user explicitly
  requests an update.
- Offline resolution uses the last valid Command Cache. With no valid cache,
  resolution fails closed with explicit guidance to start the editor service once.
- Connection or refresh failure may fall back to a last valid cache with an explicit
  stale-source indicator. It must not convert malformed live data into a valid cache.
- The CLI owns a Routing Overlay keyed only by Canonical Command ID. It may contain
  domain, Visibility Tier, intent boundaries, `prepareWith`, `verifyWith`,
  limitations, and Deny Policy entries. It may not restate executable argument or
  result contracts.
- The skill contains a compact Domain Index. It does not embed the full command
  catalog.
- Domain selection accepts repeatable domain filters and returns a deduplicated
  Discovery Set. Its Route Cards contain routing identity and concise selection
  semantics, but no argument, result, or verification schema.
- Exact selection accepts repeatable Canonical Command IDs and returns a Contract
  Bundle. Its default `canonical-agent-v2` projection contains the execution
  requirements, arguments, validation rules, and top-level result inventory while
  omitting internal wire routes, package diagnostics, repeated summaries, and
  nested result schemas. `--verbose` returns the complete normalized package-v1
  contract for diagnostics. Selected contracts are followed by one deduplicated
  layer of `prepareWith` and `verifyWith` contracts; relations on those added
  contracts are not recursively expanded.
- A selected contract retains its own limitations. Limitations are not promoted into
  separate executable commands or erased when related contracts are bundled.
- Built-in command input uses one structured object containing `id` and `args`.
  Namespace/action is no longer an accepted agent-facing built-in request shape.
  Backward compatibility with the previous agent-facing shape is not required.
- The CLI validates built-in requests using the resolved package-owned contract,
  translates the Canonical Command ID to wire namespace/action internally, and then
  uses the existing execution transport.
- The CLI does not implement lossless `decimal` transport. Preflight rejects an
  argument whose contract declares the `decimal` format with an explicit
  unsupported-type error instead of silently binding through a binary float.
- Project-defined custom commands use the same canonical-id input shape when their
  contract is available from the live registry or Command Cache.
- Batch items use Canonical Command IDs and structured arguments under the same
  resolution and validation rules as a single command.
- `editor/menu.open` and `editor/window.open` are removed from the executable package
  registry. Their intents remain in the Deny Policy, and agent guidance must not
  route them through snippets or arbitrary execution as an automatic fallback.
- `command/list`, `session/list`, `session/inspect`, and `session/reset` remain
  control-plane capabilities and do not appear in authoring Domain Index, Route Card,
  or Contract Bundle results unless a dedicated control-plane view is explicitly
  requested.
- `editor/playmode.status` is removed. `editor/status` includes current play-mode
  state and transition state sufficient to replace it.
- `transform/get` is removed. `gameobject/get` provides the transform projection
  sufficient to replace it.
- Asset import and reimport remain separate commands.
- `asset/create_folder` and `screenshot/scene_view` move from Core to Advanced.
- After removals and merges, the Agent Command Surface contains 51 authoring
  commands. The generated surface has no duplicate Canonical Command IDs or aliases.
- Natural-language command search is not included. Domain and exact-id filters remain
  deterministic.
- The package companion change lands before the rewritten CLI pull request is
  finalized. Later downstream pull requests are rebased and retested only after this
  contract is stable.

## Testing Decisions

- Tests assert behavior visible at a repository boundary rather than private class
  layout, reflection helper names, serialized field implementation, or cache helper
  calls.
- The package's primary test seam is the real Unity Editor HTTP control plane for
  the conditional Registry Snapshot operation. This verifies actual
  built-in registration, custom discovery, classification, normalization, network
  envelopes, and the mapping from Canonical Command ID to executable route.
- The package may also exercise its deterministic snapshot builder as a narrow pure
  seam. This supports focused diagnosis of canonical ordering and hashing but does
  not replace the real Unity seam.
- The CLI's primary test seam is its real top-level command entry with an injected
  registry gateway, a temporary Unity project, and a temporary user cache. Tests
  observe JSON output, exit behavior, wire dispatch, and registry call sequence.
- Small pure tests may cover normalized snapshot validation, Routing Overlay merge,
  Route Card projection, and one-layer Contract Bundle expansion. They support the
  primary seam and do not become a second integration architecture.
- Package determinism tests use deliberately reordered equivalent registrations and
  prove equal snapshots/fingerprints. Mutating one built-in contract changes only the
  built-in fingerprint; mutating one custom contract changes only the custom
  fingerprint.
- Package live tests independently recompute returned SHA-256 values from normalized
  snapshot data and verify 64-character lowercase digests.
- Package live tests prove that a representative safe built-in Canonical Command ID
  maps to the existing route and executes, while an unknown route remains a
  validation error.
- CLI cache scenarios cover missing cache, unchanged token, changed registry,
  explicit refresh, valid offline cache, missing-cache offline failure, corrupt
  cache, and failed cache replacement.
- CLI call-sequence assertions prove that a resolution performs exactly one
  conditional snapshot request, that an unchanged answer transfers no contract
  payload, and that one multi-domain or multi-id resolution performs no repeated
  live registry request.
- Discovery assertions prove that Route Cards omit executable schemas, multi-domain
  results are deduplicated, exact ids return execution-complete agent projections,
  verbose exact ids return complete normalized package contracts, relation expansion
  is one layer only, shared relations are deduplicated, and selected limitations are
  preserved.
- Execution assertions prove that built-in single and batch requests accept
  Canonical Command IDs, translate to the expected wire route, reject the old
  namespace/action agent shape, reject unknown ids, and fail preflight before
  dispatch for invalid arguments.
- Cross-implementation consistency is asserted at the contract-data level only:
  the live parity check proves the CLI's canonical writer reproduces the package's
  partition fingerprints and registry generation for the current snapshot. There
  is no case-by-case evaluator replay; CLI preflight and the package binder may
  differ on edge-case interpretation, and the binder's verdict governs.
- Surface assertions prove exactly 51 authoring commands, no aliases, no prohibited
  menu/window commands, no control-plane commands in authoring discovery, the two
  merged projections, the two Advanced tier moves, and distinct import/reimport
  routes.
- The existing 89-case routing evaluation must remain 89/89.
- The existing live contract evaluation must remain 12/12 against Unity 2022.
- Four complex workflows are evaluated with interleaving of at least three runs per
  variant. The exact model, reasoning level, CLI revision, package revision, prompts,
  raw outputs, and traces are recorded locally.
- Complex-workflow acceptance requires all four cross-domain routes correct, at
  least 14 of 16 total expectations met, and no individual workflow below the main
  baseline.
- Median input tokens and median tool output must each be no greater than 110% of
  main. Repeated domain or exact discovery count must be zero, and duplicate alias
  count must be zero.
- Evaluation graders must parse the outputs produced by the current run. Hard-coded
  historical verdicts are invalid.
- Test source, evaluation artifacts, traces, and generated reports remain local and
  uncommitted, as requested. Product and durable specification changes remain the
  only pull-request content.

## Out of Scope

- Natural-language or semantic command search.
- Hard runtime byte limits, command-count limits, or schema truncation caps.
- Caller-selected field projection, pagination, cursors, or `truncated` response
  contracts for potentially large read commands. The fixed default top-level result
  inventory is part of `canonical-agent-v2`, not a caller-selected projection API.
- Overwrite-safety changes for prefab, material, scene, or screenshot operations.
- New independent read-back commands for scene open/save, prefab unpack, material
  slot changes, console clearing, or screenshot semantics.
- Combining asset import and asset reimport.
- Compatibility aliases for the previous agent-facing namespace/action request
  shape.
- Rewriting the existing HTTP execution protocol beyond the registry control-plane
  additions and internal Canonical Command ID adaptation.
- Rebasing or modifying downstream reliability pull requests as part of the two
  registry pull requests.
- Committing local tests, evaluations, traces, or reports.

## Further Notes

- Delivery is split into a package companion pull request and a rewrite of the
  existing CLI command-routing pull request. The package change is a blocking
  dependency because the CLI must consume package-owned contracts rather than invent
  them.
- Downstream CLI pull requests 13 through 15 and package pull requests 6 through 8
  remain valid bodies of work, but must be rebased and retested after the registry
  redesign lands.
- The previous complex-workflow evidence demonstrates the regression risk but is not
  an acceptance result for this implementation. The new grader must analyze new
  outputs.
- The old first-pass prompt evaluation lacked a real executor, exact model metadata,
  token accounting, and traces. It must be rerun before being used as evidence.
- The absence of package-side test infrastructure does not reduce the live acceptance
  requirement. A Unity 2022 project is the authoritative seam for package registry
  behavior.
