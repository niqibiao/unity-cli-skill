# Unity CLI Catalog

Two operations over the custom-command catalog, both via `cs`.

## Sync the per-project custom command catalog

After registering new C# framework commands in Unity (or when `cs catalog list` looks
stale/empty), sync the catalog from the running Editor:

```bash
cs catalog sync --json
```

Parse the JSON. Report the summary (added/removed/total) and the catalog file path
from `data.catalogFile`. The catalog lives at `{project}/.unity-cli/catalog.json`
(committed — shared with the team). To read/write a different location for **one call
only**, pass `--catalog-path /your/path/catalog.json` (not persisted).

List the cached catalog offline:

```bash
cs catalog list --json
```

If sync fails, check that the Unity Editor is open and the C# Console package is
installed.

## Maintainer audit: built-in tables vs the live Editor

**Audience: plugin maintainers.** Check whether the static built-in command tables in
`references/commands.md` have drifted from the commands registered in the running
Editor (new actions added upstream, removed actions, changed signatures). This does
**not** touch the per-project custom-command catalog.

1. Fetch the live command list:
   ```bash
   cs list-commands --json
   ```
2. Parse `data.commands` (built-in + custom).
3. Compare with the static tables in `references/commands.md`. Built-in namespaces:
   editor, gameobject, component, transform, material, prefab, project, scene,
   screenshot, profiler, session, command.
4. Report differences and suggest edits to `references/commands.md`:
   - **New** commands not in the tables → add them
   - **Removed** commands in the tables but not live → remove them
   - **Changed signatures** (different args) → update them
