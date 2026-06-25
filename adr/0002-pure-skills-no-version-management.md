# ADR-0002: Pure-skills distribution — no version management, only version check

- Status: Accepted
- Date: 2026-06-25
- Deciders: niqibiao (owner), Claude Code + Codex (adversarial design review)
- Supersedes: ADR-0001 (per-project CLI version dispatch)
- References: live path-model verification on PackagesDemo (2026-06-25);
  `docs/dual-agent-support.md` §2 (now obsolete, see below)

## Context

ADR-0001 built a per-project CLI version-dispatch system — a `~/.unity-cli-plugin/store`
(one full CLI per version) plus a fixed-path dispatch shim and a per-project pin
(`.unity-cli/cli.json`) — so multiple plugin versions could coexist on one machine when
different Unity projects use packages of different `major.minor` lines. That machinery
(store / shim / copy / self-refresh / pin / package-tag auto-pin) was the bulk of the
CLI's complexity and the source of repeated edge cases (PR #6's 8 findings; an
`os.execve` Windows segfault).

The project is moving to a **pure-skills** distribution: `npx skills add` installs one
skill (`unity-cli`) whose CLI is bundled under `scripts/cli/` and runs in place. With
`--copy` the skill is committed into the project, so **the committed copy is the version
record** — multi-version coexistence is achieved by "one copy per project," not by a
global store + dispatch shim.

Two facts make the old machinery unnecessary:

1. **Per-project copy replaces per-project dispatch.** Each project carries its own
   bundled CLI (and its own committed Unity package), kept aligned by the human at commit
   time. There is nothing to dispatch.
2. **Native skills obsolete the `$HOME` shim.** The shim existed because, under the old
   *plugin-cache* distribution, Codex `cd`'d into the skill dir on a relative path and
   corrupted `--project "$(pwd)"` (`docs/dual-agent-support.md` §2, probes #1/#3). Live
   verification on 2026-06-25 (PackagesDemo, throwaway probe skill) showed that under
   `npx skills`' **native** skills, Codex behaves like Claude Code: it substitutes the
   skill's base dir to an **absolute** path, does **not** `cd`, and `$(pwd)` survives.
   The asymmetry that motivated the shim is gone.

## Decision

**Distribute as a pure skill. Drop all version *management*; keep only a runtime version
*check*.**

- **Distribution.** `npx skills add … --copy`, committed into the project. The CLI runs in
  place from `<SKILL_DIR>/scripts/cli/cs.py`. No `$HOME` store / shim / copy / self-refresh.
- **No version management.** Removed: the dispatch shim and store, the per-project CLI pin
  (`_write_project_pin` / `.unity-cli/cli.json`), the package-tag auto-pin
  (`_resolve_pin` / `find_matching_tag`), `install-cli`, and `check-update`
  (`check_versions` / `fetch_remote_version`).
- **Version check only.** At runtime the CLI warns (`⚠ version mismatch`) when the
  installed Unity package and the CLI are on different `major.minor` lines. It warns; it
  does not block. The CLI version comes from the bundled `scripts/cli/VERSION`.
- **`setup` does not install the package.** It locates the Unity project, caches the
  resolved package path, and runs the version check. The user provides the package
  (committed with the skill, or added via UPM / `manifest.json`).
- **Project detection.** No `--project` on the hot path: `find_project_root` walks up from
  the working directory, and from the CLI's own committed location (`__file__`), so it
  resolves the project regardless of an agent's cwd. `--project <path>` remains an
  override.
- **Machine-local state lives in the user's home cache**, in a per-project subdir keyed by
  a hash of the resolved project root (`%LOCALAPPDATA%\unity-cli\<key>\` /
  `$XDG_CACHE_HOME/unity-cli/<key>/`), written atomically — never in the project tree or
  the committed skill dir. Committed project state (`catalog.json`, `snippets~/`,
  `snippets-audit.json`) stays under `<project>/.unity-cli/`.

## Consequences

- **Far simpler.** The largest and most edge-prone subsystem (store/shim/pin/self-refresh)
  is gone; the CLI is a thin dispatcher again.
- **Predictable versioning by git.** A project's CLI and package versions are whatever is
  committed; upgrading is a reviewable `npx skills update` diff, not a runtime dispatch.
- **Both agents, one path.** `<SKILL_DIR>/scripts/cli/cs.py` resolves absolutely in Claude
  Code and Codex; walk-up (+ `__file__`) makes project detection independent of cwd.
- **Compatibility enforced by checking, not matching.** A mismatch warns; the user aligns
  the package (the two components still version independently).

### Residual risks / what would change this

- **Global (`-g`) install.** Installed outside any project, the `__file__` walk-up anchor
  finds nothing and a cd'd cwd may not be inside a project; pass `--project`. The
  recommended install is project-local `--copy`, where this does not arise.
- **Mixed-version machine.** Handled structurally by the per-project committed copy — each
  project runs its own CLI + package. No global reconciliation is attempted.
- **Compatibility definition** stays `major.minor` (`is_aligned`); unchanged by this ADR.

`docs/dual-agent-support.md` is **superseded** by this ADR for everything concerning the
`$HOME` shim and the plugin-cache distribution; its probe table remains of historical
interest only.
