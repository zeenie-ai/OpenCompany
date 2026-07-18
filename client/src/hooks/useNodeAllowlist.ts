import { useState, useEffect, useCallback, useRef } from 'react';
import { useWebSocket } from '../contexts/WebSocketContext';

interface NodeAllowlistResponse {
  show_all: boolean;
  enabled_nodes: string[];
  /** Mode-independent blocklist by backend group (e.g. 'android' hides
   *  every plugin in the android group). Empty array if
   *  the backend doesn't ship the field (older deployments). */
  disabled_groups: string[];
  /** Mode-independent blocklist by exact node type. Use for one-off
   *  types whose group label doesn't match (e.g. 'android_agent' is in
   *  the 'agent' group, not 'android'). */
  disabled_nodes: string[];
  /** Mode-independent blocklist for the Credentials Modal by category
   *  key (matches `category` in server/config/credential_providers.json,
   *  e.g. 'android' / 'ai' / 'social'). Empty array on older
   *  deployments. */
  disabled_credential_categories: string[];
  /** Mode-independent blocklist for the Master Skill folder
   *  dropdown — every entry hides the matching subfolder under
   *  server/skills/. Empty array on older deployments. */
  disabled_skill_folders: string[];
}

/**
 * Fetches the node allowlist from the backend and exposes the membership
 * checks every UI surface that lists nodes uses (Component Palette,
 * dropdowns, AI tool selectors, master skill folder, etc.).
 *
 * Two independent checks:
 *
 *   `isBlocked(nodeType, groups?)` — absolute blocklist. Mode-independent.
 *     Driven by `disabled_groups` + `disabled_nodes` from the JSON
 *     config. Use to turn off entire groups (e.g. android) or specific
 *     types (e.g. android_agent) so they're hidden in BOTH normal and
 *     dev mode. Pass the node's group array so disabled_groups can
 *     fire — without groups, only exact-type matches catch.
 *
 *   `isAllowed(nodeType)` — positive allowlist. Driven by `enabled_nodes`.
 *     `show_all=true` (empty list) returns true for everything. Call
 *     sites typically gate this on `!proMode` so dev mode bypasses
 *     the allowlist.
 *
 *   `isVisible(nodeType, groups?)` — convenience: `!isBlocked && isAllowed`.
 *     Use when proMode doesn't matter (every surface SHOULD respect
 *     both layers).
 *
 * While the response is loading, all checks return permissively (no
 * palette flash). Both blocklists default to empty when the backend
 * doesn't ship the fields (older deployments).
 */
export const useNodeAllowlist = () => {
  const { sendRequest, isConnected } = useWebSocket();
  const [config, setConfig] = useState<NodeAllowlistResponse | null>(null);
  const hasFetchedRef = useRef(false);

  useEffect(() => {
    if (!isConnected || hasFetchedRef.current) return;
    hasFetchedRef.current = true;

    sendRequest<NodeAllowlistResponse>('get_node_allowlist', {})
      .then((response) => {
        setConfig({
          show_all: response?.show_all ?? true,
          enabled_nodes: response?.enabled_nodes ?? [],
          disabled_groups: response?.disabled_groups ?? [],
          disabled_nodes: response?.disabled_nodes ?? [],
          disabled_credential_categories: response?.disabled_credential_categories ?? [],
          disabled_skill_folders: response?.disabled_skill_folders ?? [],
        });
      })
      .catch((error) => {
        console.error('[NodeAllowlist] Failed to fetch:', error);
        setConfig({
          show_all: true,
          enabled_nodes: [],
          disabled_groups: [],
          disabled_nodes: [],
          disabled_credential_categories: [],
          disabled_skill_folders: [],
        });
      });
  }, [isConnected, sendRequest]);

  /** Absolute blocklist check (mode-independent). False while loading
   *  so UI doesn't pre-hide nodes during the fetch round-trip. */
  const isBlocked = useCallback(
    (nodeType: string, groups?: string[] | readonly string[]): boolean => {
      if (!config) return false;
      if (config.disabled_nodes.includes(nodeType)) return true;
      if (groups && groups.length > 0) {
        for (const g of groups) {
          if (config.disabled_groups.includes(g)) return true;
        }
      }
      return false;
    },
    [config]
  );

  /** Positive allowlist check. True while loading so UI doesn't hide
   *  during the fetch. show_all=true returns true for every node. */
  const isAllowed = useCallback(
    (nodeType: string): boolean => {
      if (!config) return true;
      if (config.show_all) return true;
      return config.enabled_nodes.includes(nodeType);
    },
    [config]
  );

  /** Convenience: hidden if blocked OR not allowed. Honors both layers
   *  unconditionally — call sites that want to bypass the allowlist
   *  in dev mode should call isBlocked directly and short-circuit
   *  the allowlist when proMode is true. */
  const isVisible = useCallback(
    (nodeType: string, groups?: string[] | readonly string[]): boolean => {
      if (isBlocked(nodeType, groups)) return false;
      return isAllowed(nodeType);
    },
    [isBlocked, isAllowed]
  );

  /** True if the credential category is in the absolute blocklist —
   *  use to filter the Credentials Modal's category list (e.g. hide
   *  the entire 'android' panel without removing it from the backend
   *  catalogue). False while loading so the modal doesn't pre-hide. */
  const isCredentialCategoryDisabled = useCallback(
    (categoryKey: string): boolean => {
      if (!config) return false;
      return config.disabled_credential_categories.includes(categoryKey);
    },
    [config]
  );

  /** True if the skill folder is in the absolute blocklist — use to
   *  filter the Master Skill folder dropdown (e.g. hide
   *  'android_agent' when the android feature is disabled). False
   *  while loading so the dropdown doesn't pre-hide. */
  const isSkillFolderDisabled = useCallback(
    (folderName: string): boolean => {
      if (!config) return false;
      return config.disabled_skill_folders.includes(folderName);
    },
    [config]
  );

  return {
    isVisible,
    isBlocked,
    isAllowed,
    isCredentialCategoryDisabled,
    isSkillFolderDisabled,
  };
};
