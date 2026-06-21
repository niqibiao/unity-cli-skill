---
name: unity-cli-setup
description: >
  Install the Unity C# Console package into the current Unity project and
  bootstrap the cross-agent CLI. Use when setting up unity-cli for the first
  time, when status reports the package is NOT FOUND, or when the user asks to
  install / set up / 安装 the Unity CLI. Works in both Claude Code and Codex.
---

# Unity CLI Setup

One-time setup: bootstrap the stable CLI path, then install the Unity package.

## 1. Bootstrap the CLI (one-time)

Run **verbatim, without changing directory** — the `||` fallback covers a first
run under Codex where `${CLAUDE_PLUGIN_ROOT}` is unavailable:

```bash
python "${CLAUDE_PLUGIN_ROOT}/cli/cs.py" install-cli || python "../../cli/cs.py" install-cli
```

This copies the CLI to `$HOME/.unity-cli-plugin/current/` so every subsequent
command uses one stable, agent-agnostic path. After a plugin upgrade the copy
refreshes itself automatically — this step is only needed the first time.

## 2. Install the Unity package

Ask the user to choose an installation method:

1. **git** (recommended) — writes the git URL to `manifest.json`; Unity resolves it on its own.
2. **local** — clones the repo into the project (for development/debugging; uses an existing local package path if found, otherwise defaults to `Packages/`).

Then run from the now-stable path:

```bash
python "$HOME/.unity-cli-plugin/current/cli/cs.py" setup --project "$(pwd)" --method <local|git>
```

By default the package is pinned to the latest `vMAJOR.MINOR.*` tag matching the
plugin version. Append `--no-pin` to install from HEAD instead. With
`--method local`, the clone ends in detached HEAD at the pinned tag; if the user
wants to develop in the clone, instruct them to `git checkout main` afterward.

**If setup fails (non-zero exit), stop immediately.** Do not retry or attempt a
manual git clone. Report the error and ask the user to resolve the underlying
issue (network, proxy, git config) before retrying.

**Version mismatch handling:** if the output contains `⚠ version mismatch`, do
NOT just report it — ask the user whether to update the package now. If they
confirm, re-run the setup command with `--update` appended.

## 3. Verify

After a successful run (no version mismatch), tell the user — keep it short, do
not paste the long CLI path:

> Open the Unity Editor for this project and wait for the Package Manager to
> resolve `com.zh1zh1.csharpconsole`. Once it's resolved, tell me and I'll check
> status.

When the user confirms Unity has resolved the package, run the
**unity-cli-status** skill to verify package resolution and service connectivity.

Do **not** promise the service will be reachable: `status` is the check, not a
guarantee — the editor HTTP service is only reachable once the C# Console
service is running in the editor. Report whatever `status` actually returns.
