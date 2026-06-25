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
- **Self-evolving snippet library** — project-local C# snippets (`.md` files, no compilation) with validation gate, usage tracking, and aging. Discover and grow via `cs snippets`.


### 🚀 Quick Start

```bash
# 1. Install the skill into your Unity project (real files, committable).
#    Run from the project root; npx auto-detects your agent(s) — Claude Code
#    (.claude/skills/) and Codex (.agents/ + .codex/skills/).
npx skills add niqibiao/unity-cli-skill --copy

# 2. Make sure the Unity C# Console package is in the project — commit it with the
#    skill, or add it via Unity Package Manager ("Add package from git URL"):
#      https://github.com/niqibiao/unity-csharpconsole.git
#    Then, in your agent:
> set up unity-cli      # locate project + version-check (does NOT install the package)

# 3. Verify
> check unity-cli status
```

> Works identically in **Claude Code and Codex** — one skill folder, run in place,
> no marketplace or plugin. There is no `~/.unity-cli-plugin` and no bootstrap step;
> the committed skill copy is the CLI.

**Prerequisites:** [Claude Code](https://claude.ai/code) or [Codex CLI](https://github.com/openai/codex) 0.139+, Node.js (for `npx`), Unity 2022.3+, Python 3.7+

### 🔒 Team / version notes

No version-management machinery (no plugin pinning, no CLI dispatch). Install with
`npx skills add niqibiao/unity-cli-skill --copy` and **commit the skill folder and
the Unity package together** — git is the version record, so everyone who pulls gets
the same, aligned pair. Keep the CLI (`skills/unity-cli/scripts/cli/VERSION`) and the
Unity package on the same `major.minor`; a `⚠ version mismatch` warning means they
drifted. See [ADR-0002](adr/0002-pure-skills-no-version-management.md).

### 💬 Usage

Just tell Claude what you want:

```
> Add a directional light and rotate it 45 degrees on X
> Find all "Enemy" objects and list their components
> Take a screenshot of the Scene View
> Start profiler recording with deep profiling
```

Claude picks the right command or writes C# code as needed.

#### 🧩 One skill, many subcommands

Everything ships in **one skill** (`unity-cli`); its `cs` subcommands cover every
operation, and the agent triggers it automatically (Claude Code and Codex alike):

| Subcommand | Description |
| ---------- | ----------- |
| `cs setup` | Locate project + version-check (does **not** install the package) |
| `cs status` / `cs health` | Package and service status |
| `cs command <ns> <action>` | Structured Unity Editor commands |
| `cs exec` | Run raw C# in the Editor (fallback) |
| `cs refresh` | Trigger asset refresh / recompile |
| `cs catalog sync` / `cs list-commands` | Custom-command catalog + maintainer audit |
| `cs snippets …` | Reusable C# snippet library |
| `cs snippets doctor` | Snippet library health audit |


#### 💻 Direct CLI

`cs` = `python "<SKILL_DIR>/scripts/cli/cs.py"` (the committed skill's CLI). No
`--project` — the project is auto-detected.

```bash
cs exec --json "Debug.Log(\"Hello\")"
cs command --json gameobject create '{"name":"Cube","primitiveType":"Cube"}'
cs refresh --json --exit-playmode --wait 60
cs batch --json '[{"ns":"gameobject","action":"create","args":{"name":"A"}},{"ns":"gameobject","action":"create","args":{"name":"B"}}]'
cs list-commands --json --timeout 10
cs catalog sync --json
cs snippets search "physics" --json
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
| `package: NOT FOUND`   | Install `com.zh1zh1.csharpconsole` (commit it or add via UPM), then run `cs setup`        |
| Port conflict          | Service auto-advances to the next free port. Check `Temp/CSharpConsole/refresh_state.json` |
| Commands not found     | Ensure the package compiled successfully (no errors in Unity Console)                      |
| Version mismatch       | Run `cs status` to see versions; align the Unity package with the CLI `major.minor`        |


---

## License

[Apache-2.0](LICENSE)

---

If this plugin saves you time, consider giving it a star. It helps others find it.