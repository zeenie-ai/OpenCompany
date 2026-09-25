/**
 * Apply the user's saved UI defaults (editor sidebar, palette and console
 * visibility, auto-save preferences) from the server row, once per page
 * load, as soon as the socket connects.
 *
 * Once per page load, not per mount: the editor now unmounts when the owner
 * switches to Normal mode, and re-applying the defaults on every return
 * would undo the panels they had toggled since.
 */

import { useEffect } from 'react';
import { useAppStore } from '../store/useAppStore';
import { useWorkflowSettingsStore } from '../stores/workflowSettingsStore';
import { useWebSocketActions } from '../contexts/WebSocketContext';

let applied = false;

/** Forget that the defaults were applied. Tests only. */
export function resetUIDefaultsForTests(): void {
  applied = false;
}

interface UIDefaultsRow {
  sidebar_default_open?: boolean;
  component_palette_default_open?: boolean;
  console_panel_default_open?: boolean;
  auto_save?: boolean;
  auto_save_interval?: number;
}

export function useUIDefaultsOnce(): void {
  const { isConnected, sendRequest } = useWebSocketActions();

  useEffect(() => {
    if (!isConnected || applied) return;
    applied = true;
    (async () => {
      try {
        const response = await sendRequest<{ settings?: UIDefaultsRow }>('get_user_settings', {});
        const row = response?.settings;
        if (!row) return;
        useAppStore.getState().applyUIDefaults({
          sidebarDefaultOpen: row.sidebar_default_open,
          componentPaletteDefaultOpen: row.component_palette_default_open,
          consolePanelDefaultOpen: row.console_panel_default_open,
        });
        const current = useWorkflowSettingsStore.getState().settings;
        useWorkflowSettingsStore.getState().patchSettings({
          autoSave: row.auto_save ?? current.autoSave,
          autoSaveInterval: row.auto_save_interval ?? current.autoSaveInterval,
          sidebarDefaultOpen: row.sidebar_default_open ?? current.sidebarDefaultOpen,
          componentPaletteDefaultOpen: row.component_palette_default_open ?? current.componentPaletteDefaultOpen,
          consolePanelDefaultOpen: row.console_panel_default_open ?? current.consolePanelDefaultOpen,
        });
      } catch (error) {
        // Retry on the next connect; the panels keep their local state.
        applied = false;
        console.error('[AppShell] Failed to load UI defaults:', error);
      }
    })();
  }, [isConnected, sendRequest]);
}
