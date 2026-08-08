# Unity CLI Automation

Shared language for evaluating how reliably an agent turns a Unity automation intent
into the intended state in a live Unity Editor or Player.

## Evaluation

**Effective Completion Rate**:
The share of benchmark tasks that reach the intended Unity state without human
correction. This is the primary product KPI.
_Avoid_: Effective trigger rate, tool success rate

**Skill Activation Recall**:
The share of Unity automation benchmark tasks for which the agent loads the
`unity-cli` skill. This diagnoses discovery only, not command execution or completion.
_Avoid_: Trigger rate

**Command Routing Accuracy**:
For benchmark tasks already covered by a built-in command, the share whose first
selected route uses the canonical `namespace.action`. This diagnoses routing only, not
execution or final completion.
_Avoid_: Command success rate, tool-call success rate

## Command Visibility

**Agent Command Surface**:
The subset of package capabilities exposed through Route Cards and Canonical Command
Contracts for Unity authoring tasks. It excludes control-plane and denied operations.
_Avoid_: Full registry, package command set, all supported commands

**Deny Policy**:
An agent-facing rule that recognizes a prohibited automation intent without offering
an executable command contract or permitting fallback around the restriction.
_Avoid_: Blocked command, hidden command, unsupported command

**Visibility Tier**:
The level at which a supported command is exposed to an agent. A tier changes default
discovery and documentation loading, not whether the underlying command exists.
_Avoid_: Command availability, permission level

**Domain Index**:
The compact, default list of Unity capability domains used to choose which reference
to load. It exposes domains such as scene or prefab, not the full action catalog.
_Avoid_: Command list, tool catalog

**Route Card**:
A compact command candidate used during discovery. It carries routing identity and
intent boundaries, never the argument, result, or verification schema.
_Avoid_: Command descriptor, summary contract, domain schema

**Discovery Set**:
The deduplicated Route Cards selected from one or more domains at one visibility
tier for a task. It is consumed once before contract selection.
_Avoid_: Full registry, command catalog, repeated domain lookup

**Canonical Command Contract**:
The sole agent-facing execution contract for one selected command. It contains the
information needed to construct and verify that command without compatibility aliases.
_Avoid_: Exact schema, live descriptor, full domain contract

**Canonical Command ID**:
The single built-in command identity shared by discovery and agent requests. Unity
wire routing is an internal concern and is not a second agent-facing identity.
_Avoid_: Namespace/action pair, command alias, wire id

**Registry Snapshot**:
The package's versioned, machine-generated serialization of its executable
contract registry, returned by the conditional snapshot operation. It is never
edited by hand and is not persisted as a repository artifact.
_Avoid_: Command manifest, hand-maintained schema, routing overlay

**Registry Fingerprint**:
A SHA-256 digest of a normalized built-in or custom registry partition. It identifies
schema equality independently of package version or registration order.
_Avoid_: Package version, cache timestamp, MD5

**Registry Generation**:
An opaque token derived from the partition Registry Fingerprints and package
version. The CLI echoes it verbatim in conditional snapshot requests and never
recomputes it.
_Avoid_: Monotonic counter, semantic version, fingerprint recomputation

**Command Cache**:
The latest live registry retained as machine-local project state and refreshed only
when the Registry Generation changes or an explicit refresh is requested.
_Avoid_: Registry Snapshot, source of truth, conversation context

**Routing Overlay**:
Curated selection guidance and Command Relations keyed by Canonical Command ID. It
contains agent-facing semantics that the execution registry does not own.
_Avoid_: Schema copy, registry patch, command manifest

**Command Relation**:
A bounded relationship from one selected command to the commands that prepare or
verify it, together with limits it cannot guarantee. It is not an end-to-end workflow.
_Avoid_: Workflow recipe, dependency closure, fallback chain

**Contract Bundle**:
The selected Canonical Command Contracts plus one deduplicated layer of contracts
required to prepare or verify them.
_Avoid_: Recursive closure, workflow pack, full domain schema

**Core Command**:
A command included in the default agent-facing index for routine Unity authoring and
verification. It must be common across projects, unambiguous, bounded in parameters
and side effects, operationally reliable, and verifiable through read-back or state.
_Avoid_: Basic command, always-loaded command

**Advanced Command**:
A supported command discovered only after a matching domain intent because it is
specialized, higher-risk, lower-frequency, or schema-heavy.
_Avoid_: Hidden command, unsupported command

**Control-Plane Command**:
A command that manages the CLI, sessions, catalogs, snippets, or command discovery
rather than directly authoring Unity project state.
_Avoid_: Unity command, admin command

The Domain Index is domain-level, while Visibility Tiers are assigned per action
inside each domain. Loading a domain reference does not promote every action in that
domain to Core.
