<div align="center">

# unity-cli-plugin

**AI coding agent plugin for Unity Editor — supports Claude Code & Codex CLI**<br/>
**Powered by [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Unity](https://img.shields.io/badge/Unity-2022.3%2B-black.svg?logo=unity)](https://unity.com/)
[![Claude Code](https://img.shields.io/badge/Claude_Code-blueviolet.svg?logo=anthropic)](https://claude.ai/code)

40+ commands for scene editing, components, assets, screenshots, profiling, and more.<br/>
Depends on **[unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)** — a Roslyn-powered interactive C# REPL for Unity.

[Quick Start](#-quick-start--claude-code) · [Usage](#-usage) · [Commands](#-commands) · [Custom Commands](#-custom-commands) · [Architecture](#️-architecture)

English | [中文](README_zh.md)

</div>

---

```
You:    "Create 10 cubes in a circle and add Rigidbody to each"
Claude: Done. 10 cubes created at radius 5, each with a Rigidbody component.
```

### ⚡ CLI + Skills

CLI commands exposed through Claude Code's skill system.

- **Token-efficient.** Skills load on demand.
- **Unrestricted.** Falls back to a full [Roslyn C# REPL](https://github.com/niqibiao/unity-csharpconsole) — not limited to predefined tools.
- **No sidecar.** Service runs inside Unity Editor. No extra process.
- **Workflow-aware.** Understands Unity's compile lifecycle, play mode, domain reload.
- **Automatic custom command discovery.** User-defined C# commands are synced into the skill catalog.
- **Runtime / IL2CPP support.** Works with HybridCLR for runtime builds.
- **Self-evolving snippet library** — project-local C# snippets (`.md` files, no compilation) with validation gate, usage tracking, and aging. Discover and grow via `cs snippets` and the `unity-cli-snippets` skill.


### 🚀 Quick Start — Claude Code

**Prerequisites:** [Claude Code](https://claude.ai/code), Unity 2022.3+, Python 3.7+

```bash
# 1. Add the marketplace & install the plugin
claude plugin marketplace add niqibiao/unity-cli-plugin
claude plugin install unity-cli-plugin

# 2. Install the Unity package (inside your project) — just ask Claude:
claude
> set up unity-cli

# 3. Verify
> check unity-cli status
```

### 🤖 Quick Start — Codex CLI

All functionality ships as skills shared by both agents.

**Prerequisites:** [Codex CLI](https://github.com/openai/codex) 0.139+, Unity 2022.3+, Python 3.7+

```bash
# 1. Add the marketplace & install the plugin
codex plugin marketplace add niqibiao/unity-cli-plugin
codex plugin add unity-cli-plugin@unity-cli-plugin

# 2. Install the Unity package (inside your project) — just ask Codex:
codex
> set up unity-cli

# 3. Verify
> check unity-cli status
```

### 🔒 Team Version Management

Pin one version across the whole team and roll everyone forward by editing
committed files. There are **three version knobs** — keep them at the same
`major.minor` (patch numbers may differ per repo) to avoid `⚠ version mismatch`.

**1. Claude Code plugin** — commit to `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "unity-cli-plugin": {
      "source": { "source": "github", "repo": "niqibiao/unity-cli-plugin", "ref": "v1.5.1" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": { "unity-cli-plugin@unity-cli-plugin": true }
}
```

`source.ref` (tag/commit) locks the version; `autoUpdate: true` re-syncs each member to the committed `ref` at session start (self-heals drift, no manual `/plugin`). The version goes in `source.ref`, **not** the `enabledPlugins` key — keys are `plugin-id@marketplace-id` with no version syntax.

**2. Codex CLI plugin** — commit to `.agents/plugins/marketplace.json`:

```json
{
  "name": "unity-cli-pinned",
  "plugins": [
    {
      "name": "unity-cli-plugin",
      "source": { "source": "git-subdir", "url": "https://github.com/niqibiao/unity-cli-plugin.git", "path": "plugin", "ref": "v1.5.1" }
    }
  ]
}
```

The `git-subdir` source's `ref` (tag) or `sha` (commit) pins the version; `path` is the plugin's subdir in the repo. Codex has **no `autoUpdate` equivalent**: after cloning, each member installs + reloads once (`/plugin install`, then `/reload-plugins`, or restart Codex). A later `ref` bump needs `codex plugin marketplace upgrade` + reload.

**3. Unity package** — pinned in `Packages/manifest.json` (managed by `cs setup`):

```json
{ "dependencies": { "com.zh1zh1.csharpconsole": "https://github.com/niqibiao/unity-csharpconsole.git#v1.5.0" } }
```

**To roll the team forward:** bump each pin to its repo's new tag (keep the plugin and package at the same `major.minor`), commit, and push. On their next session, Claude members auto-update; Codex members run one `marketplace upgrade` + reload; Unity re-resolves the package when the Editor opens.

> Claude (`autoUpdate`) is fully transparent. Codex pins the source but does **not** auto-install on clone — the first install and each upgrade need a manual reload/restart.

### 💬 Usage

Just tell Claude what you want:

```
> Add a directional light and rotate it 45 degrees on X
> Find all "Enemy" objects and list their components
> Take a screenshot of the Scene View
> Start profiler recording with deep profiling
```

Claude picks the right command or writes C# code as needed.

#### 🧩 Skills

Everything is a skill — Claude triggers them automatically based on what you ask
(works in both Claude Code and Codex):

| Skill                         | Description                                  |
| ----------------------------- | -------------------------------------------- |
| `unity-cli-setup`             | Install the Unity package (cross-agent bootstrap) |
| `unity-cli-status`            | Check package and service status             |
| `unity-cli-refresh`           | Trigger asset refresh / recompile            |
| `unity-cli-refresh-commands`  | Refresh per-project custom command cache     |
| `unity-cli-sync-catalog`      | Audit built-in tables vs live Editor (maintainer) |
| `unity-cli-command`           | Structured Unity Editor commands             |
| `unity-cli-exec-code`         | Run raw C# in the Editor (fallback)          |
| `unity-cli-snippets`          | Reusable C# snippet library                  |
| `unity-cli-snippets-audit`    | Snippet library health audit                 |


#### 💻 Direct CLI

```bash
python plugin/cli/cs.py exec --json --project . "Debug.Log(\"Hello\")"
python plugin/cli/cs.py command --json --project . gameobject create '{"name":"Cube","primitiveType":"Cube"}'
python plugin/cli/cs.py refresh --json --project . --exit-playmode --wait 60
python plugin/cli/cs.py batch --json --project . '[{"ns":"gameobject","action":"create","args":{"name":"A"}},{"ns":"gameobject","action":"create","args":{"name":"B"}}]'
python plugin/cli/cs.py list-commands --json --project . --timeout 10
python plugin/cli/cs.py catalog sync --json --project .
python plugin/cli/cs.py catalog list --json --project .
python plugin/cli/cs.py snippets list --json --project .
python plugin/cli/cs.py snippets search "physics" --json --project .
```

### 📦 Commands

50 built-in commands across 13 namespaces. All commands support `--json` output.

#### gameobject


| Action       | Description                                           |
| ------------ | ----------------------------------------------------- |
| `find`       | Find GameObjects by name, tag, or component type      |
| `create`     | Create a new GameObject (empty or primitive)          |
| `destroy`    | Destroy a GameObject                                  |
| `get`        | Get detailed info about a GameObject                  |
| `modify`     | Change name, tag, layer, active state, or static flag |
| `set_parent` | Reparent a GameObject                                 |
| `duplicate`  | Duplicate a GameObject                                |


#### component


| Action   | Description                              |
| -------- | ---------------------------------------- |
| `add`    | Add a component to a GameObject          |
| `remove` | Remove a component from a GameObject     |
| `get`    | Get serialized field data of a component |
| `modify` | Modify serialized fields of a component  |


#### transform


| Action | Description                                           |
| ------ | ----------------------------------------------------- |
| `get`  | Get position, rotation, and scale                     |
| `set`  | Set position, rotation, and/or scale (local or world) |


#### scene


| Action      | Description                                                       |
| ----------- | ----------------------------------------------------------------- |
| `hierarchy` | Get the full scene hierarchy tree, optionally with component info |


#### prefab


| Action        | Description                                   |
| ------------- | --------------------------------------------- |
| `create`      | Create a prefab asset from a scene GameObject |
| `instantiate` | Instantiate a prefab into the active scene    |
| `unpack`      | Unpack a prefab instance                      |


#### material


| Action   | Description                                         |
| -------- | --------------------------------------------------- |
| `create` | Create a new material asset with a specified shader |
| `get`    | Get material properties from an asset or a Renderer |
| `assign` | Assign a material to a Renderer component           |


#### screenshot


| Action       | Description                             |
| ------------ | --------------------------------------- |
| `scene_view` | Capture the Scene View to an image file |
| `game_view`  | Capture the Game View to an image file  |


#### profiler


| Action   | Description                                        |
| -------- | -------------------------------------------------- |
| `start`  | Start Profiler recording (optional deep profiling) |
| `stop`   | Stop Profiler recording                            |
| `status` | Get current Profiler state                         |
| `save`   | Save recorded profiler data to a `.raw` file       |


#### editor


| Action            | Description                         |
| ----------------- | ----------------------------------- |
| `status`          | Get editor state and play mode info |
| `playmode.status` | Get current play mode state         |
| `playmode.enter`  | Enter play mode                     |
| `playmode.exit`   | Exit play mode                      |
| `menu.open`       | Execute a menu item by path         |
| `window.open`     | Open an editor window by type name  |
| `console.clear`   | Clear the editor console            |
| `console.mark`    | Write a searchable marker to the editor log |


#### asset


| Action          | Description                           |
| --------------- | ------------------------------------- |
| `move`          | Move or rename an asset               |
| `copy`          | Copy an asset to a new path           |
| `delete`        | Delete one or more assets             |
| `create_folder` | Create a folder in the Asset Database |


#### project


| Action           | Description                      |
| ---------------- | -------------------------------- |
| `scene.list`     | List all scenes in the project   |
| `scene.open`     | Open a scene by path             |
| `scene.save`     | Save the current scene           |
| `selection.get`  | Get the current editor selection |
| `selection.set`  | Set the editor selection         |
| `asset.list`     | List assets by type filter       |
| `asset.import`   | Import an asset by path          |
| `asset.reimport` | Reimport an asset by path        |


#### session


| Action    | Description                             |
| --------- | --------------------------------------- |
| `list`    | List active REPL sessions               |
| `inspect` | Inspect a session's state               |
| `reset`   | Reset a session's compiler and executor |


#### command


| Action | Description                                      |
| ------ | ------------------------------------------------ |
| `list` | List all registered commands (built-in + custom) |


#### snippets


| Action       | Description                                          |
| ------------ | ---------------------------------------------------- |
| `list`       | Browse the local snippet library                     |
| `show`       | Show a snippet's full content and metadata           |
| `search`     | Search snippets by keyword                           |
| `use`        | Run a snippet (executes its C# code)                 |
| `add`        | Add a new snippet to the library                     |
| `update`     | Update an existing snippet                           |
| `deprecate`  | Mark a snippet as deprecated                         |
| `prune`      | Remove aged-out or deprecated snippets               |
| `stats`      | Show usage statistics for the snippet library        |


### 🔧 Custom Commands

Custom commands are supported. See [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole) for how to define and register them.

The plugin maintains a persistent per-project catalog of custom commands. Run `cs catalog sync` to pull the latest list from Unity and cache it to disk; run `cs catalog list` to view the cached catalog offline without connecting to the Editor.

### 🏗️ Architecture

```
Claude Code                      Unity Editor
┌──────────────────┐            ┌──────────────────────────┐
│  Skills          │            │  com.zh1zh1.csharpconsole│
│  ┌────────────┐  │            │  ┌────────────────────┐  │
│  │ cli-command│──┼── HTTP ──▶ │  │ ConsoleHttpService │  │
│  │ cli-exec   │  │            │  │  ├─ CommandRouter  │  │
│  └────────────┘  │            │  │  ├─ REPL Compiler  │  │
│                  │            │  │  └─ REPL Executor  │  │
│  Python CLI      │            │  └────────────────────┘  │
│  ┌────────────┐  │            │                          │
│  │ cs.py      │  │            │  40+ CommandActions      │
│  │ core_bridge│  │            │  (GameObject, Component, │
│  └────────────┘  │            │   Prefab, Material, ...) │
└──────────────────┘            └──────────────────────────┘
```

- **Plugin layer**: Skills invoked by Claude Code and Codex
- **CLI layer**: Python dispatcher, serializes requests to JSON
- **Unity layer**: [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole) — HTTP service, auto-discovered command handlers, Roslyn C# REPL

Auto-detects project root and service port. No manual configuration.

### ❓ Troubleshooting


| Problem                | Solution                                                                                   |
| ---------------------- | ------------------------------------------------------------------------------------------ |
| `service: UNREACHABLE` | Make sure Unity Editor is open with the project loaded                                     |
| `package: NOT FOUND`   | Run the `unity-cli-setup` skill, or check `Packages/manifest.json`                         |
| Port conflict          | Service auto-advances to the next free port. Check `Temp/CSharpConsole/refresh_state.json` |
| Commands not found     | Ensure the package compiled successfully (no errors in Unity Console)                      |
| Version mismatch       | Run the `unity-cli-status` skill to check version info. Update the package if protocol differs |


---

## License

[Apache-2.0](LICENSE)

---

If this plugin saves you time, consider giving it a star. It helps others find it.