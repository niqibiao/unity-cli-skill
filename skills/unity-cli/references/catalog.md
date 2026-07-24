# Unity CLI Catalog

Two operations over the custom-command catalog, both via `cs`. The catalog is
custom-command state only. Consult it at the custom-command stage defined in
`SKILL.md`; built-in discovery belongs to `references/commands.md`.

## Sync the per-project custom command catalog

After registering new C# framework commands in Unity (or when `cs catalog list` looks
stale/empty), sync the catalog from the running Editor:

```bash
cs catalog sync
```

Report the summary it prints (added/removed/total) and the catalog file path.
The catalog lives at `{project}/.unity-cli/catalog.json`
(committed — shared with the team). To read/write a different location for **one call
only**, pass `--catalog-path /your/path/catalog.json` (not persisted).

List the cached catalog offline (text index: id, arg names, summary — for full arg
types/descriptions, Read the catalog file directly):

```bash
cs catalog list
```

If sync fails, check that the Unity Editor is open and the C# Console package is
installed.

## Maintainer audit: static contracts vs the live Editor

**Audience: skill maintainers.** Check whether the canonical static contracts in
`scripts/cli/command_manifest.json` have drifted from commands registered in the
running Editor. This does **not** touch the per-project custom-command catalog.

1. Fetch the live command list:
   ```bash
   cs list-commands --json
   ```
2. Parse `data.commands` (built-in + custom).
3. Compare live built-ins with the 59 manifest contracts, including the 57
   routable contracts and the retained blocked `editor/menu.open` and
   `editor/window.open` entries.
4. Report differences and suggest manifest updates:
   - **New** live commands missing from the manifest;
   - **Removed** live commands still present in the manifest;
   - **Changed signatures** whose live args differ from the static contract;
   - **Unclassified** contracts missing domain, tier, intent boundary, or
     verification metadata.
