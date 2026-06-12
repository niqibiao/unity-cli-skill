<div align="center">

# unity-cli-plugin

**AI coding agent plugin for Unity Editor — supports Claude Code & Codex CLI**<br/>
**Powered by [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole)**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Unity](https://img.shields.io/badge/Unity-2022.3%2B-black.svg?logo=unity)](https://unity.com/)
[![Claude Code](https://img.shields.io/badge/Claude_Code-blueviolet.svg?logo=anthropic)](https://claude.ai/code)
[![Codex CLI](https://img.shields.io/badge/Codex_CLI-00A67E.svg?logo=openai)](https://github.com/niqibiao/unity-cli-plugin/tree/codex-plugin)

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


### 🚀 Quick Start — Claude Code

**Prerequisites:** [Claude Code](https://claude.ai/code), Unity 2022.3+, Python 3.7+

```bash
# 1. Add the marketplace & install the plugin
claude plugin marketplace add niqibiao/unity-cli-plugin
claude plugin install unity-cli-plugin

# 2. Install the Unity package (inside your project)
claude
> /unity-cli-setup

# 3. Verify
> /unity-cli-status
```

### 🚀 Quick Start — Codex CLI (Experimental)

> **Note:** Codex CLI does not yet officially support third-party plugin distribution. The steps below rely on the local workspace marketplace, which may change in future releases.

**Prerequisites:** [Codex CLI](https://github.com/openai/codex), Unity 2022.3+, Python 3.7+

```bash
# 1. Start Codex in your Unity project
cd /path/to/your/unity-project
codex

# 2. Tell Codex to clone and install the plugin:
#    "Clone https://github.com/niqibiao/unity-cli-plugin/tree/codex-plugin
#     then run install.sh with the current directory"

# 3. Restart Codex, find and install the plugin
/plugins              # locate unity-cli-plugin → Install

# 4. Restart Codex again, then initialize the Unity package
$unity-cli-setup

# 5. Verify
$unity-cli-status
```

<details>
<summary>Manual install / already have a marketplace</summary>

```bash
cd /path/to/your/unity-project
git clone --depth=1 -b codex-plugin https://github.com/niqibiao/unity-cli-plugin.git plugins/unity-cli-plugin
rm -rf plugins/unity-cli-plugin/.git
```

Then add to `.agents/plugins/marketplace.json`:

```jsonc
{
  "name": "local-workspace",
  "plugins": [{
    "name": "unity-cli-plugin",
    "source": { "source": "local", "path": "./plugins/unity-cli-plugin" },
    "policy": { "installation": "INSTALLED_BY_DEFAULT", "authentication": "ON_INSTALL" },
    "category": "Productivity"
  }]
}
```
</details>

### 💬 Usage

Just tell Claude what you want:

```
> Add a directional light and rotate it 45 degrees on X
> Find all "Enemy" objects and list their components
> Take a screenshot of the Scene View
> Start profiler recording with deep profiling
```

Claude picks the right command or writes C# code as needed.

#### ⌨️ Slash Commands


| Command                       | Description                                  |
| ----------------------------- | -------------------------------------------- |
| `/unity-cli-setup`            | Install the Unity package                    |
| `/unity-cli-status`           | Check package and service status             |
| `/unity-cli-refresh`          | Trigger asset refresh / recompile            |
| `/unity-cli-refresh-commands` | Refresh per-project custom command cache     |
| `/unity-cli-sync-catalog`     | Audit built-in tables in SKILL.md vs live Editor (maintainer) |


#### 💻 Direct CLI

```bash
python cli/cs.py exec --json --project . "Debug.Log(\"Hello\")"
python cli/cs.py command --json --project . gameobject create '{"name":"Cube","primitiveType":"Cube"}'
python cli/cs.py refresh --json --project . --exit-playmode --wait 60
python cli/cs.py batch --json --project . '[{"ns":"gameobject","action":"create","args":{"name":"A"}},{"ns":"gameobject","action":"create","args":{"name":"B"}}]'
python cli/cs.py list-commands --json --project . --timeout 10
python cli/cs.py catalog sync --json --project .
python cli/cs.py catalog list --json --project .
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

- **Plugin layer**: Skills and slash commands invoked by Claude Code
- **CLI layer**: Python dispatcher, serializes requests to JSON
- **Unity layer**: [unity-csharpconsole](https://github.com/niqibiao/unity-csharpconsole) — HTTP service, auto-discovered command handlers, Roslyn C# REPL

Auto-detects project root and service port. No manual configuration.

### ❓ Troubleshooting


| Problem                | Solution                                                                                   |
| ---------------------- | ------------------------------------------------------------------------------------------ |
| `service: UNREACHABLE` | Make sure Unity Editor is open with the project loaded                                     |
| `package: NOT FOUND`   | Run `/unity-cli-setup` or check `Packages/manifest.json`                                   |
| Port conflict          | Service auto-advances to the next free port. Check `Temp/CSharpConsole/refresh_state.json` |
| Commands not found     | Ensure the package compiled successfully (no errors in Unity Console)                      |
| Version mismatch       | Run `/unity-cli-status` to check version info. Update the package if protocol differs      |


---

## License

[Apache-2.0](LICENSE)

---

If this plugin saves you time, consider giving it a star. It helps others find it.