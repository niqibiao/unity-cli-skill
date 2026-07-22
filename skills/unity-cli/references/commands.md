# Unity CLI Command

Run framework commands in the Unity Editor via the C# Console command protocol.

## Command-First Principle

Always prefer `cs command` over `cs exec` when a built-in framework command exists. Only fall back to `cs exec` for ad-hoc C# that no existing command covers.

If no built-in or custom command matches the task, **next** check `cs snippets` (run `cs snippets search <description>`) before falling back to ad-hoc `cs exec` via `cs exec`. The decision order is: command → snippet → ad-hoc.

## Usage

Write the request to a JSON file (your file tool handles all escaping), then:

```bash
cs command --json --input req.json
```

`req.json` is a single object — `{"ns": "<namespace>", "action": "<action>", "args": { … }}`
(`args` omitted for no-arg commands). `--input -` reads the JSON from stdin instead.

## Argument & Result Conventions

- Always pass `--json` on `command` / `list-commands` / `batch` / `complete` — their result payload is only emitted as JSON. The envelope is `{ "ok": bool, "exitCode": int, "summary": str, "data": {...} }` — check `ok` / `exitCode` for success. (Other subcommands print an equivalent text form; leave `--json` off there.)
- `data` is already structured. For `cs command`, it's the command's own result object; for `cs list-commands`, it's `{ "commands": [...] }`. Do not expect or re-parse a `resultJson` string field — that only appears with `--verbose`.
- `Vector3` args are JSON objects: `{"x":0,"y":1,"z":3}`. Same for `rotation` and `scale`.
- Array args (e.g. `instanceIds: int[]`, `assetPaths: string[]`) are JSON arrays.
- `bool` args accept `true` / `false`; some legacy fields (`active`, `isStatic`) take `int` (0/1) — see the per-action signature.

## Asset Refresh

After writing `.cs` files or modifying assets on disk, trigger a refresh so Unity recompiles. `cs refresh` wraps the full procedure (play-mode check, exit if needed, refresh, wait). For direct CLI use:

```bash
cs refresh --wait --exit-playmode
```

REPL sessions are cleared on domain reload.

## Identifier Convention

Many commands accept both `path` (hierarchy path like `"Canvas/Button"`) and `instanceId` (int). Use whichever is available — `path` for human-readable references, `instanceId` when you have it from a prior command result. You never need both.

## Built-in Command Catalog

### editor

| action | summary | args |
|--------|---------|------|
| status | Get editor state and play mode info | — |
| playmode.status | Get current play mode state | — |
| playmode.enter | Enter play mode | — |
| playmode.exit | Exit play mode | — |
| menu.open | Open a menu item by path | menuPath: string |
| window.open | Open an editor window by type name | typeName: string, utility: bool |
| console.clear | Clear the editor console | — |
| console.mark | Write a searchable marker into the editor log and return the log file path | label: string |

### gameobject

| action | summary | args |
|--------|---------|------|
| find | Find GameObjects by name, tag, or component type | name: string, tag: string, componentType: string |
| create | Create a new GameObject (empty or primitive) | name: string, primitiveType: string, parentPath: string |
| destroy | Destroy a GameObject | path: string, instanceId: int |
| get | Get detailed info about a GameObject | path: string, instanceId: int |
| modify | Modify a GameObject's basic properties | path: string, instanceId: int, name: string, tag: string, layer: int, active: int, isStatic: int |
| set_parent | Change a GameObject's parent | path: string, instanceId: int, parentPath: string, parentInstanceId: int, worldPositionStays: bool |
| duplicate | Duplicate a GameObject | path: string, instanceId: int, newName: string |

### component

| action | summary | args |
|--------|---------|------|
| add | Add a component to a GameObject | typeName: string, gameObjectPath: string, gameObjectInstanceId: int |
| remove | Remove a component from a GameObject | typeName: string, gameObjectPath: string, gameObjectInstanceId: int, index: int |
| get | Get serialized field data of a component | typeName: string, gameObjectPath: string, gameObjectInstanceId: int, index: int |
| modify | Modify serialized fields of a component | fields: FieldPair[], typeName: string, gameObjectPath: string, gameObjectInstanceId: int, index: int |

### transform

| action | summary | args |
|--------|---------|------|
| get | Get a GameObject's transform values | path: string, instanceId: int |
| set | Set a GameObject's transform values | path: string, instanceId: int, position: Vector3, rotation: Vector3, scale: Vector3, local: bool |

### material

| action | summary | args |
|--------|---------|------|
| create | Create a new material asset | savePath: string, shaderName: string |
| get | Get material properties | assetPath: string, gameObjectPath: string |
| assign | Assign a material to a Renderer component | materialPath: string, gameObjectPath: string, gameObjectInstanceId: int, index: int |

### prefab

**Scene instance operations:**

| action | summary | args |
|--------|---------|------|
| create | Create a prefab asset from a scene GameObject | savePath: string, gameObjectPath: string, gameObjectInstanceId: int |
| instantiate | Instantiate a prefab into the active scene | assetPath: string, parentPath: string, position: Vector3 |
| unpack | Unpack a prefab instance | gameObjectPath: string, gameObjectInstanceId: int, full: bool |

**Direct asset editing** (edit the `.prefab` file without instantiating — `assetPath` is the asset path, `gameObjectPath` is the relative path within the prefab hierarchy):

| action | summary | args |
|--------|---------|------|
| asset_get | Get detailed info about a GameObject in a prefab asset | assetPath: string, gameObjectPath: string |
| asset_hierarchy | Get the hierarchy tree of a prefab asset | assetPath: string, depth: int, includeComponents: bool |
| asset_add_component | Add a component to a GameObject in a prefab asset | assetPath: string, typeName: string, gameObjectPath: string |
| asset_get_component | Get serialized properties of a component in a prefab asset | assetPath: string, typeName: string, gameObjectPath: string, index: int |
| asset_modify_component | Modify serialized fields of a component in a prefab asset | fields: AssetFieldPair[], assetPath: string, typeName: string, gameObjectPath: string, index: int |
| asset_remove_component | Remove a component from a GameObject in a prefab asset | assetPath: string, typeName: string, gameObjectPath: string, index: int |
| asset_add_gameobject | Add a child GameObject to a prefab asset | assetPath: string, parentPath: string, name: string |
| asset_modify_gameobject | Modify a GameObject's properties in a prefab asset | assetPath: string, gameObjectPath: string, name: string, tag: string, layer: int, active: int, isStatic: int |
| asset_remove_gameobject | Remove a child GameObject from a prefab asset | assetPath: string, gameObjectPath: string |

### project

| action | summary | args |
|--------|---------|------|
| scene.list | List all scenes in the project | — |
| scene.open | Open a scene by path | scenePath: string, mode: string |
| scene.save | Save the current scene | scenePath: string, saveAsCopy: bool |
| selection.get | Get the current editor selection | — |
| selection.set | Set the editor selection by name or path | instanceIds: int[], assetPaths: string[] |
| asset.list | List assets by type filter | filter: string, folders: string[] |
| asset.import | Import an asset by path | assetPath: string, forceSynchronousImport: bool |
| asset.reimport | Reimport an asset by path | assetPath: string, forceSynchronousImport: bool |

### asset

| action | summary | args |
|--------|---------|------|
| move | Move or rename an asset | sourcePath: string, destinationPath: string |
| copy | Copy an asset to a new path | sourcePath: string, destinationPath: string |
| delete | Delete one or more assets | assetPath: string, assetPaths: string[] |
| create_folder | Create a folder in the Asset Database | folderPath: string |

### scene

| action | summary | args |
|--------|---------|------|
| hierarchy | Get the full scene hierarchy tree | depth: int, includeComponents: bool |

### screenshot

| action | summary | args |
|--------|---------|------|
| scene_view | Capture the current Scene View | savePath: string, width: int, height: int |
| game_view | Capture the Game View | savePath: string, width: int, height: int, superSize: int |

### profiler

| action | summary | args |
|--------|---------|------|
| start | Start Profiler recording | deep: bool, logFile: string |
| stop | Stop Profiler recording | — |
| status | Get current Profiler state | — |
| save | Save recorded profiler data to a .raw file | savePath: string |

### session

| action | summary | args |
|--------|---------|------|
| list | List active REPL sessions | — |
| inspect | Inspect a session's state | — |
| reset | Reset a session's compiler and executor | — |

### command

| action | summary | args |
|--------|---------|------|
| list | List registered commands | — |

## Custom Commands

Lookup order for custom (user-defined) commands:

1. Check the static built-in catalog in this SKILL.md (the tables above).
2. Read the per-project catalog cache via `cs catalog list` (text index: id, arg names, summary). For full arg types/descriptions, Read the catalog file directly (default `{project}/.unity-cli/catalog.json`, committed).
3. If the catalog is empty, missing, or stale, fall back to a live query:
   ```bash
   cs list-commands --type custom --json
   ```
4. If a catalog refresh is needed, run `cs catalog sync`.

### Catalog commands

| command | description |
|---------|-------------|
| `cs catalog sync` | Pull the current custom command list from Unity and write/update the per-project catalog JSON |
| `cs catalog list` | Display the contents of the per-project catalog JSON |

### `list-commands` `--type` flag

Pass `--type` to filter results:
- `--type builtin` — built-in framework commands only
- `--type custom` — user-registered custom commands only
- *(omit `--type`)* — all commands

## Runtime Mode

Most commands are **editor-only** (require the Unity Editor, not a standalone player). The `session/*` and `command/list` commands work in both editor and runtime modes. Pass `--mode runtime --port 15500` for player builds.

## Examples

Each block below is the `req.json` content; run it with `cs command --json --input req.json`:

```json
{"ns":"editor","action":"status"}
{"ns":"gameobject","action":"create","args":{"name":"Wall","primitiveType":"Cube"}}
{"ns":"transform","action":"set","args":{"path":"Wall","position":{"x":0,"y":1,"z":3}}}
{"ns":"component","action":"get","args":{"gameObjectPath":"Main Camera","typeName":"Camera"}}
{"ns":"screenshot","action":"scene_view","args":{"savePath":"Assets/screenshot.png"}}
{"ns":"scene","action":"hierarchy","args":{"depth":3,"includeComponents":true}}
{"ns":"prefab","action":"asset_hierarchy","args":{"assetPath":"Assets/Prefabs/Player.prefab","depth":2,"includeComponents":true}}
{"ns":"prefab","action":"asset_add_component","args":{"assetPath":"Assets/Prefabs/Player.prefab","typeName":"BoxCollider","gameObjectPath":"Body"}}
```

Discovery / catalog (no payload — plain flags, no `--input`):

```bash
cs list-commands --json
cs list-commands --type custom --json
cs catalog sync
cs catalog list
```

## Workflow

1. Match the user's intent to a namespace + action from the catalog above
2. Run the command with appropriate args
3. **After writing C# files**, follow the Asset Refresh procedure above (check play mode → exit if needed → refresh)
4. If no matching command exists in the built-in catalog, run `cs catalog list` to check the per-project custom-command cache
5. If the cache is empty or stale, run `cs list-commands --type custom` as a live fallback, then `cs catalog sync`
6. If no command covers the request at all, fall back to `cs exec`
