# Node Allowlist — single-config UI visibility

`server/config/node_allowlist.json` is the single source of truth for hiding nodes, credential panels, and skill folders from the UI. Backed by `NodeAllowlistService` (server) + `useNodeAllowlist` hook (frontend). One JSON edit propagates to every UI surface.

## Why this exists

Removing a feature from OpenCompany at the registry level (delete the plugin file) breaks workflows that already reference it — existing saved canvases can't load, exported JSON breaks on re-import. The allowlist hides the UI affordance (palette + credentials + skills picker) without touching the registry, so existing references still resolve while new ones can't be created.

## Config shape

```json
{
  "enabled_nodes": [...],
  "disabled_groups": [...],
  "disabled_nodes": [...],
  "disabled_credential_categories": [...],
  "disabled_skill_folders": [...]
}
```

Five independent lists, two tiers of enforcement:

| Field | Enforced in | Mode-gated? |
|---|---|---|
| `enabled_nodes` | Normal-mode Hire (server, `is_hire_allowed`) while `featureFlags.normalMode` is on (the default); the ComponentPalette's Normal filter when it is off | Flag on: the editor palette ignores it. Flag off: normal mode applies it, dev/pro mode bypasses |
| `disabled_groups` | ComponentPalette, Hire | **No — always enforced** |
| `disabled_nodes` | ComponentPalette, Hire | **No — always enforced** |
| `disabled_credential_categories` | CredentialsModal (filters category headers + their providers) | **No — always enforced** |
| `disabled_skill_folders` | MasterSkillEditor folder dropdown | **No — always enforced** |

`show_all` is auto-derived: `enabled_nodes` empty → `show_all: true` (positive list inactive, everything visible in normal mode). The four disable lists are absolute blocklists that win over both `show_all` and dev mode.

## Normal mode: what Hire may build

With the Normal-mode screen on (client `featureFlags.normalMode`, on by
default; `VITE_NORMAL_MODE=false` turns it off), the editor is Dev mode and its palette lists every
node the blocklists allow; `enabled_nodes` no longer filters it. The list
governs what Normal mode's Hire may assemble into an employee instead:
`services.node_allowlist.is_hire_allowed(node_type)` returns true when the
type is not blocked (exact type, or any of its groups, the same matching
the palette uses) and either `enabled_nodes` is empty or lists it. The graph
builder checks every node it emits, and node types never come from the hire
payload. The node types the v1 app registry (`server/config/employee_apps.json`)
can emit are locked in by `server/tests/test_node_allowlist_hire.py`, so a
missing entry fails CI rather than a hire.

## Frontend hook API

```ts
const {
  isVisible,                       // (type, groups?) => boolean — convenience: !isBlocked && isAllowed
  isBlocked,                       // (type, groups?) => boolean — absolute blocklist, mode-independent
  isAllowed,                       // (type) => boolean — positive allowlist; show_all=true returns true
  isCredentialCategoryDisabled,    // (categoryKey) => boolean — for CredentialsModal
  isSkillFolderDisabled,           // (folderName) => boolean — for MasterSkillEditor
} = useNodeAllowlist();
```

Loading state defaults permissively for all checks so the UI doesn't pre-hide nodes / categories during the WS round-trip.

## Adding a new disable

Edit `server/config/node_allowlist.json` only. No code change. Example — disable WhatsApp:

```json
{
  "disabled_groups": ["email", "whatsapp"],
  "disabled_credential_categories": ["email", "whatsapp"],
  "disabled_skill_folders": ["social_agent"]
}
```

Re-enable: drop the entries. The hook + filters are forward-compatible with empty / missing fields.

## What's currently disabled

Nothing is blocklisted. All four disable lists (`disabled_groups`, `disabled_nodes`, `disabled_credential_categories`, `disabled_skill_folders`) are empty in the shipped `server/config/node_allowlist.json`; the `email` group + credential category that this section once listed as disabled are no longer blocked. The only active list is `enabled_nodes` — the positive allowlist (see the table above for where it applies). Read the live entry count from the JSON rather than from this doc.

Android service nodes are enabled and connect directly to an agent's
`input-tools` handle. The Android Agent, Android credential category, and
bundled Android skills are also visible. The former `androidTool` aggregator
is obsolete; legacy workflows are normalized to direct service-to-agent edges.

Task Manager is an intrinsic, non-removable capability of Orchestrator and AI
Employee nodes. It is intentionally absent from the normal palette and Agent
Builder catalogue; historical explicit nodes remain compatible.

## agentBuilder integration

`agentBuilder` honors the same allowlist when surfacing its spawnable catalogue (`inspect_canvas.available_tools` / `available_agents` / `available_skills`) and rejecting `add_tool` / `add_subagent` / `add_skill` calls:

| Allowlist field | agentBuilder filter |
|---|---|
| `disabled_nodes` | Excluded from `_allowed_tool_types()` + `_allowed_subagent_types()` — the LLM can't spawn or list the type. |
| `disabled_groups` | Excluded from both sets via plugin `group` tuple intersection (any matching entry hides every plugin in the group). |
| `disabled_skill_folders` | Excluded from `_catalogue_skills()` — the LLM doesn't see folders the operator marked. Match is on the SkillMetadata path ancestor (e.g. `disabled_skill_folders: ["android_agent"]` blocks every skill under `server/skills/android_agent/`). |

Read via `services.node_allowlist.get_node_allowlist_service().get_config()` — same singleton the UI hook hits. Adding a node to `disabled_nodes` once propagates to BOTH the UI palette AND the LLM's spawnable surface.

## Filter call sites

| File | Filter |
|---|---|
| `client/src/components/ui/ComponentPalette.tsx` | `if (isBlocked(name, groups)) return false; if (!proMode && !isAllowed(name)) return false;` |
| `client/src/components/credentials/CredentialsModal.tsx` | `providers.filter(p => !isCredentialCategoryDisabled(p.category))` + same for `categories` |
| `client/src/components/parameterPanel/MasterSkillEditor.tsx` | `(foldersQuery.data ?? []).filter(f => !isSkillFolderDisabled(f.name))` |

## Backend service

`server/services/node_allowlist.py` reads + parses the JSON via the shared `_str_list()` helper that rejects non-string entries. WS handler `get_node_allowlist` returns the full config dict to the frontend; the hook caches via `useRef` so the fetch runs once per session.
