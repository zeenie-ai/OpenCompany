/**
 * The Settings panel's working copy of the user's workflow settings
 * (auto-save, UI defaults, agent limits). The server row
 * (`get_user_settings`) is the source of truth; this copy is what the
 * controlled panel edits, cached in localStorage (`workflow_settings`) so it
 * opens pre-filled before the row arrives. Previously Dashboard-local state,
 * lifted so the shell can host the panel for either screen.
 */

import { create } from 'zustand';
import { defaultSettings, type WorkflowSettings } from '../components/ui/settingsPanel/schema';

const STORAGE_KEY = 'workflow_settings';

function load(): WorkflowSettings {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? { ...defaultSettings, ...JSON.parse(saved) } : defaultSettings;
  } catch {
    return defaultSettings;
  }
}

function persist(settings: WorkflowSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Storage blocked: the in-memory copy still works for this session.
  }
}

interface WorkflowSettingsState {
  settings: WorkflowSettings;
  setSettings: (next: WorkflowSettings) => void;
  /** Merge a partial update (the UI-defaults load at boot). */
  patchSettings: (patch: Partial<WorkflowSettings>) => void;
}

export const useWorkflowSettingsStore = create<WorkflowSettingsState>((set) => ({
  settings: load(),
  setSettings: (next) => {
    persist(next);
    set({ settings: next });
  },
  patchSettings: (patch) =>
    set((state) => {
      const next = { ...state.settings, ...patch };
      persist(next);
      return { settings: next };
    }),
}));
