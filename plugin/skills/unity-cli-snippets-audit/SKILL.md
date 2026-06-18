---
name: unity-cli-snippets-audit
description: >
  Health audit and anti-rot cleanup for the project's snippet library
  (.unity-cli/snippets~/). Use when the user asks to check, audit, clean up,
  or "防腐化" the snippet library, after a Unity version upgrade (API drift),
  when snippets feel stale or keep failing, or as periodic maintenance.
  Drives `cs snippets doctor` and triages its findings into cleanup actions.
---

# Unity CLI Snippets Audit

Anti-rot maintenance for the snippet library. The library rots in four ways:
**API drift** (Unity upgrade breaks bodies), **integrity drift** (files and
audit entries diverge via merges / hand edits), **staleness** (cold, never
reused), and **zombie deprecations** (retired but never removed). This skill
detects all four and funnels cleanup through the CLI.

> **Running `cs`:** below, `cs` is shorthand for
> `python "$HOME/.unity-cli-plugin/current/cli/cs.py"` — one stable path, run
> verbatim without changing directory. If it's not installed yet, run the
> **unity-cli-setup** skill once first.

## Workflow

1. **Diagnose (offline, always safe):**
   ```bash
   python "$HOME/.unity-cli-plugin/current/cli/cs.py" snippets doctor --json --project "$(pwd)"
   ```
2. **Revalidate (requires running Unity — this is what catches API drift):**
   ```bash
   python "$HOME/.unity-cli-plugin/current/cli/cs.py" snippets doctor --revalidate --json --project "$(pwd)"
   ```
   Re-runs the validation gate on every live `read-only` snippet and refreshes
   `verified_at` on passes. Run after every Unity version upgrade. Doctor runs
   never touch usage stats — diagnostics are not invocations.
3. **Triage** each finding per the table below. Fix what you can; ask the user
   only for destructive choices.
4. **Report**: findings, actions taken, anything left for the user.

## Triage table

| Finding | Meaning | Action |
|---------|---------|--------|
| `revalidation_failed` | body no longer compiles/runs (likely API drift) | diagnose with `cs snippets show <id>`; fix body → `cs snippets update <id> --file <md>`; unfixable → `deprecate --reason "API drift <unity-version>"` |
| `broken` | ≥5 Run-body failures over ≥7d | same as above — it slipped past use-time auto-deprecation |
| `corrupt` / `id_mismatch` | file no longer parses / declares wrong id | `show` it; repair via `update --file`; hopeless → deprecate |
| `orphan_file` | file with no audit entry (merge artifact) | if valuable: `cs snippets add <id> --file .unity-cli/snippets~/<id>.md` (validates + registers); else delete the file |
| `missing_file` | audit entry whose file is gone | restore from git history, or hand-remove the entry from `snippets-audit.json` (plain project state) |
| `unverified` | mutates snippet never validated | review the body once; keep or deprecate |
| `cold` | unused 90d, <3 successes | **ask the user**; approved → `cs snippets prune --cold` |
| `removable` | deprecated >30d ago | **ask the user**; approved → `cs snippets prune --remove` |

## DO NOT

- Run `prune --remove` or `prune --cold` without user confirmation — they are
  the only destructive paths.
- Treat `revalidation_failed` as a usage failure — doctor never touches stats,
  and neither should you.
- Hand-edit snippet bodies in `.unity-cli/snippets~/` — repairs go through
  `cs snippets update --file` so the validation gate runs.
- Deprecate `cold` snippets on your own judgment — cold is informational;
  the user owns curation.

## When to trigger proactively

- After running the `unity-cli-setup` skill with `--update`, or any Unity version change: run step 2.
- If two different snippets fail in one session: run step 1 before continuing.
