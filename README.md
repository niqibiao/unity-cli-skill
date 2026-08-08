<div align="center">

# unity-cli

**AI coding agent skill for Unity Editor — works with any skills-compatible agent (Claude Code, Codex, …)**<br/>
**Powered by [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Unity](https://img.shields.io/badge/Unity-2022-black.svg?logo=unity)](https://unity.com/)
[![Claude Code](https://img.shields.io/badge/Claude_Code-blueviolet.svg?logo=anthropic)](https://claude.ai/code)

57 package-owned built-ins: 51 authoring commands across six default domains and
6 explicit control-plane commands.<br/>
Depends on **[unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)** — a Roslyn-powered interactive C# REPL for Unity.

[Quick Start](#-quick-start) · [Usage](#-usage) · [Commands](#-commands) · [Custom Commands](#-custom-commands) · [Architecture](#️-architecture)

English | [中文](README_zh.md)

</div>

---

```text
You:    "Create 10 cubes in a circle and add Rigidbody to each"
Agent:  Done. 10 cubes created at radius 5, each with a Rigidbody component.
```

### ⚡ CLI + Skills

CLI commands are exposed through the agent's skill system.

- **Token-efficient.** Domain Index → Route Cards → Contract Bundle keeps
  unrelated command schemas out of context.
- **Package-aligned.** One fingerprint comparison resolves package-owned built-in
  and project custom contracts into a per-project machine cache.
- **Unrestricted.** Falls back to a full
  [Roslyn C# REPL](https://github.com/niqibiao/unity-csharpconsole) when no
  structured command or reusable snippet fits.
- **No sidecar.** The service runs inside Unity Editor with no extra process.
- **Workflow-aware.** Understands Unity's compile lifecycle, play mode, and domain
  reload.
- **Runtime / IL2CPP support.** Works with HybridCLR for runtime builds.
- **Self-evolving snippet library.** Project-local C# snippets have a validation
  gate, usage tracking, and aging.

### 🚀 Quick Start

> [!IMPORTANT]
> **Install scope = the Unity project, not global.** Never install into your home
> or global skills directory. The bundled CLI locates its Unity project by walking
> up from its own committed location.

**1 · Install the `unity-cli` skill:**

```bash
cd path/to/your/UnityProject
npx skills add niqibiao/unity-cli-skill --copy
```

**2 · Initialize:**

In your AI agent, run **`unity-cli setup`**.

**Prerequisites:** a skills-compatible agent (for example
[Claude Code](https://claude.ai/code) or
[Codex CLI](https://github.com/openai/codex)), Node.js for `npx`, Unity 2022,
and Python 3.10+.

### 💬 Usage

Tell your agent what you want:

```text
> Add a directional light and rotate it 45 degrees on X
> Find all "Enemy" objects and list their components
> Take a screenshot of the Scene View
> Start profiler recording with deep profiling
```

The agent discovers the smallest relevant command contract, verifies mutations,
and writes C# only when a structured route does not fit.

#### 🧩 One skill, many subcommands

Everything ships in one skill (`unity-cli`):

| Subcommand | Description |
|---|---|
| `cs setup` | Install/version-check the Unity package |
| `cs status` / `cs health` | Inspect package and service state |
| `cs list-commands` | Progressively discover package-owned contracts |
| `cs command --input` | Preflight and run one canonical command |
| `cs batch --input` | Preflight and run a command workflow in one request |
| `cs exec --file` | Run raw C# as the final fallback |
| `cs refresh` | Refresh assets and wait for compilation |
| `cs catalog sync` / `cs catalog list` | Maintain the shared custom-command shortlist |
| `cs snippets …` | Browse and maintain reusable C# snippets |

### 📦 Commands

The Unity package is the executable schema authority. The CLI does not maintain a
second built-in argument/result manifest. Instead it combines the current package
Registry Snapshot with a small schema-free routing overlay.

Progressive discovery has three stages:

```bash
# 1. Optional Domain Index: skip when the relevant domains are already clear
cs list-commands --offline --json

# 2. First live discovery: scoped Route Cards, still no argument/result schemas
cs list-commands \
  --domain objects --domain assets --tier core --json

# 3. Contract Bundle: selected contracts + one direct relation layer
cs list-commands --offline \
  --id gameobject/create \
  --id gameobject/get \
  --json
```

The first live discovery in an agent session performs one fingerprint comparison.
Later queries use `--offline` and the validated project cache. `--refresh` forces a
complete snapshot only when the user explicitly asks to update the command list.

#### Default authoring domains

| Domain | Scope |
|---|---|
| `editor` | Editor readiness, play mode, and Console diagnostics |
| `scene` | Scene discovery, loading, saving, and hierarchy |
| `objects` | GameObjects, components, transforms, and selection |
| `assets` | Project assets and materials |
| `prefabs` | Prefab creation, instantiation, inspection, and direct editing |
| `capture` | Scene/Game View capture and Profiler recording |

The six registry/session mechanics are visible only through the explicit control
view:

```bash
cs list-commands --offline \
  --view control --domain control --tier control-plane --json
```

Execute the stable canonical ID; the package contract owns its internal wire route:

```json
{"id":"gameobject/create","args":{"name":"Wall","primitiveType":"Cube"}}
```

Pass the JSON through `cs command --json --input <file>`. Built-in and custom
commands use the same package-owned preflight. The CLI rejects stale execution
contracts, unknown arguments, invalid types/ranges, ambiguous selectors, and
unsafe empty mutations before Unity runs them.

`editor/menu.open` and `editor/window.open` are deny-policy intents, not executable
contracts. Exact-ID discovery returns them as non-executing `denied` decisions;
the skill will not bypass them through snippets or raw C#.

#### Snippets

| Action | Description |
|---|---|
| `list` / `show` / `search` | Discover reusable snippets |
| `use` | Run a snippet |
| `add` / `update` | Validate and maintain snippets |
| `deprecate` / `prune` | Retire snippets |
| `stats` / `doctor` | Audit usage and library health |

### 🔧 Custom Commands

Custom commands use the same package registry, canonical-ID discovery, preflight,
and execution path as built-ins:

```bash
cs list-commands --view custom --json
cs list-commands --offline --view custom --id teamtools/build_room --json
```

See [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole) for
defining and registering them.

`cs catalog sync` writes a deterministic, version-controlled shortlist from a
registry verified during that invocation. `cs catalog list` reads the shortlist
offline; the current package Registry Snapshot remains execution authority.

### 🏗️ Architecture

```text
AI Agent
  └─ unity-cli skill
      └─ pure-stdlib Python CLI
          ├─ schema-free routing overlay
          ├─ fingerprint resolver + machine-local Registry Snapshot cache
          ├─ progressive discovery + package-contract preflight
          └─ HTTP bridge
              └─ com.zh1zh1.csharpconsole in Unity Editor/Player
                  ├─ package-owned registry (51 authoring + 6 control)
                  ├─ command handlers
                  └─ Roslyn compiler / REPL executor
```

The CLI dynamically imports its client core from the installed Unity package, so
client and service stay on the same `major.minor` line. Project root and service
port are auto-detected.

### ❓ Troubleshooting

| Problem | Solution |
|---|---|
| `service: UNREACHABLE` | Open Unity Editor with the project loaded |
| `package: NOT FOUND` | Run `cs setup`, then let Unity resolve the package |
| Port conflict | The service advances to a free port; inspect `Temp/CSharpConsole/refresh_state.json` |
| Custom unavailable offline | Run one live `cs list-commands --view custom --json` |
| Version mismatch | Use `cs status`, then align package and CLI `major.minor` |

---

## License

[Apache-2.0](LICENSE)

---

If this skill saves you time, consider giving it a star. It helps others find it.
