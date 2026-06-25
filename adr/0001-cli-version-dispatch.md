# ADR-0001: Per-project CLI version dispatch — explicit pin, never auto-decide

- Status: Accepted
- Date: 2026-06-20
- Deciders: niqibiao (owner), Claude Code + Codex (adversarial design review)
- Supersedes: the pin/`.pending` auto-management introduced on `feat/versioned-store-shim` (PR #6)
- References: `cc-codex-discussion-history/20260620-104903-ucp-version-dispatch-review.md`; PR #6 review threads (8 findings)

## Context

The plugin keeps one full CLI per version in a store (`~/.unity-cli-plugin/store/<version>/cli`)
and a fixed-path dispatch shim (`~/.unity-cli-plugin/current/cli/cs.py`) that picks which store
version runs for a given project. This exists so multiple plugin versions can coexist on one
machine (different Unity projects may use packages of different `major.minor` lines).

The first implementation recorded a per-project pin (`<project>/.unity-cli/cli.json`) **and**
tried to *auto-manage* it: a `store/.pending` hint, a `major.minor` "highest patch" fallback,
preferring `.pending` for `setup`, and auto-writing/​migrating the pin on `setup`. PR #6's review
surfaced **8 edge-case findings in a row**, essentially all rooted in that auto-management of the
pin and the global `.pending` state. A CC↔Codex adversarial review converged on the diagnosis and,
after the owner's direction, on this decision.

**The two components version independently.** `unity-cli-plugin` (this repo, the CLI) and
`com.zh1zh1.csharpconsole` (the Unity package) each release on their own cadence. The relationship
between them is **compatibility**, not version equality. The system must therefore *ensure
compatibility*, not make versions track each other.

## Decision

**A pinned project runs its pin verbatim and never drifts. An unpinned project runs the version that
best matches its installed package. The system never moves a *pinned* version; the user changes a
project's version by running `setup`.**

| Command class | Which store CLI runs | Writes the pin? |
|---------------|----------------------|-----------------|
| **runtime** (`exec`, `command`, `health`, `refresh`, `list-commands`, `complete`, `batch`, `catalog`, `snippets`, …), **`status`**, and a bare invocation | the project's **pin**, verbatim, when it is set and present in the store. Otherwise (no pin, or the pinned version isn't installed) the **optimal** version: the store CLI aligned (`major.minor`) with the project's installed Unity package, highest patch — else the newest. | no |
| **`setup`** | **newest** store entry (the version the user just invoked / bootstrapped). On any incompatibility/version mismatch it **prompts; the user decides** (upgrade / keep / explicit `--source URL#vX.Y.Z`). | only after it installs/updates **and** the running CLI is `major.minor`-aligned with the package it selected; otherwise it clears any stale pin (see *pin-alignment* below) |
| **`install-cli`** | newest (store-level maintenance) | no |

A **pin is never overridden or moved** — that is the stability guarantee. The "optimal" pick applies
*only* when there is no usable pin (an unpinned legacy project, a never-set-up project, or a pin
whose version was removed from the store): it reads the installed package and runs a compatible CLI
rather than erroring, so the project just works. Because dispatch degrades gracefully this way,
`status` and a bare `cs` need no special case, and `setup` needs no migration pin. The "installed
package version" is read from `Packages/packages-lock.json` first (the version Unity actually
resolved — a bare semver for registry deps, a `URL#vX.Y.Z` for git deps), then a manifest `file:`
dep or embedded package, then `Library/PackageCache`; an **ambiguous** cache (multiple distinct
versions, no lock) **fails closed** to the newest CLI rather than guessing by filesystem order.

**Pin-alignment (why `setup` doesn't blindly pin newest).** `setup` runs as the newest store CLI,
but pins to it *only when newest is `major.minor`-aligned with the package the user selected*. A
deliberate off-line install — `--source URL#v1.5.2` under a newer (1.6) CLI — would otherwise pin
1.6 verbatim and bypass the package-aligned optimal pick, freezing a mismatch the user didn't ask
for. In that case `setup` writes **no** pin and **clears any stale one**, so the optimal pick runs a
compatible store CLI once Unity resolves the package (and a runtime `⚠ version mismatch` warns until
an aligned CLI exists). The normal case — newest CLI installs a newest-aligned package — pins as
before. Clearing is safe because the optimal pick reconstructs the same compatible choice from the
installed package.

Compatibility is also a **runtime check**: when the running CLI is not aligned with the installed
package, the CLI **warns** (`⚠ version mismatch`) and the user runs `setup` to resolve it.

`setup` cannot be dispatched by the project's existing pin — doing so runs the *old* CLI, which
re-pins the old version and makes upgrades impossible (PR #6 finding #1). Hence `setup`/`install-cli`
run as `newest`. The `_subcommand` parser (distinguishing `setup`/`install-cli` from the rest) and
canonical Unity-root resolution (to locate the pin *and* the package) are retained.

`setup` also **picks up a newer source**: if the source plugin was upgraded in place but not yet
re-deposited, the newest store entry's self-refresh — *only* under `setup` (a maintenance command) —
delegates to the newer source, depositing it and running as it, so an upgrade from the stable path
isn't blocked by a stale store. Runtime commands never cross-version-refresh (that would hijack a
pinned project); a store entry write that fails surfaces as a failed `setup`/`install-cli` **before
the project manifest is mutated** — `setup` aborts on a nonzero copy rather than half-succeeding
(package changed, but the fixed-path entry point missing/stale), and the same applies on the
`setup` self-refresh path — rather than a silently broken shim.

### Removed (relative to the auto-managed-pin attempt that drew PR #6's findings)

- `store/.pending` and every preference based on it.
- Treating a pin as a *moving* target — a pin no longer falls back to "highest patch of its line";
  it is used exactly or not at all. (`major.minor` is still used, but only to match an *unpinned*
  project's installed package against the store — never to drift a pin.)
- Auto-correcting/auto-moving a *pinned* project to follow the package.
- `install-cli --gc` auto-prune. With verbatim pins it is unsafe — pruning a patch could orphan a
  project's exact pinned version, and safe pruning would need every project's pin, which isn't
  discoverable. The store keeps all bootstrapped versions (each a small Python tree); explicit,
  user-named pruning can be a future feature if disk ever matters.

## Consequences

- **Simpler and predictable.** Dispatch is `pin → that exact version` (and, only when there is no
  usable pin, the version matching the installed package). The 8 findings were almost all about
  pin/`.pending` synchronization timing; removing the synchronization removes the class.
- **Stable.** A *pinned* project stays on its pinned version (down to the patch) until the user
  changes it — no surprise jumps just because a newer version appeared in the store or the package
  moved. (An *unpinned* project tracks its installed package, so it may move as the store gains
  matching patches — but that is the "you never pinned it" case, and `setup` pins it the moment it
  installs/updates.)
- **User owns version choices.** Upgrades, downgrades, and version selection happen only through
  `setup` (with `--source URL#vX.Y.Z` for an explicit version), always with a prompt on mismatch.
- **Compatibility is enforced by checking, not matching**, honoring the independent release cadences.

### Residual risks / what would change this

- **Unpinned project with no aligned CLI in the store.** An unpinned project whose installed package
  has no matching `major.minor` CLI in the store falls back to the newest CLI and runs with a
  `⚠ version mismatch` warning (it cannot do better — no compatible CLI exists). The user resolves it
  by installing the aligned version (`setup --update`, or `--source URL#vX.Y.Z`). When an aligned CLI
  *is* present, the unpinned project runs it correctly with no warning.
- **Multi-agent, newer-in-store-than-invoked.** If another agent deposited a newer version, `setup`
  runs as `newest`, which may exceed the version the current agent invoked. Mitigation: `setup`
  prompts before changing anything, so it is still the user's decision; not silent. Eliminating it
  would require carrying the desired version explicitly (env/flag), which conflicts with the fixed
  cross-agent call contract — **not adopted now**.
- **Compatibility definition.** Currently `major.minor` equality (`is_aligned`). If the protocol is
  later shown compatible across minors, this can widen to a `protocolVersion` range (the service
  already advertises `protocolVersion`); if a patch can break the protocol, it narrows to exact.
  The dispatch model is unaffected either way.

## Alternatives rejected

- **Auto-track the installed package version for *pinned* projects** (let the shim follow the package
  and silently re-point a project the user already pinned). Rejected: it moves a version the user
  fixed, violating the stability guarantee. (Package matching *is* used — but only to choose a
  version for an *unpinned* project, where there is nothing to move.)
- **Keep patching the auto-managed pin** (continue resolving findings #7/#8/… on PR #6). Rejected:
  it does not converge — each patch exposed the next edge.
