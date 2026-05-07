/**
 * CommandPaletteHost — Dashboard-level command list registration.
 *
 * Owns the canonical command set that the global ⌘K palette surfaces.
 * Receives every action as a handler prop (Dashboard already has them
 * in scope) plus the active theme controls; assembles a `CommandItem[]`
 * with stable IDs, hints, and keyboard shortcuts and renders the
 * underlying CommandPalette.
 *
 * New shell action: add a handler to `Handlers`, wire it through from
 * Dashboard, append a `CommandItem` to `commands` below. No edits to
 * the CommandPalette primitive itself.
 */

import * as React from 'react';
import {
  Settings as SettingsIcon,
  KeyRound,
  Save,
  Play,
  Square,
  FilePlus,
  FolderOpen,
  PanelLeftClose,
  PanelRightClose,
  Terminal,
  Palette as PaletteIcon,
  Download,
  Upload,
} from 'lucide-react';
import { CommandPalette, type CommandItem } from './CommandPalette';
import { AVAILABLE_THEMES, useTheme, type ThemeName } from '../../contexts/ThemeContext';

interface Handlers {
  save: () => void;
  newWorkflow: () => void;
  open: () => void;
  run: () => void;
  stop: () => void;
  isDeploying: boolean;
  exportFile: () => void;
  importJSON: () => void;
  openSettings: () => void;
  openCredentials: () => void;
  toggleSidebar: () => void;
  toggleComponentPalette: () => void;
  toggleConsolePanel: () => void;
}

const THEME_LABEL: Record<ThemeName, string> = {
  light: 'Light',
  dark: 'Dark',
  renaissance: 'Renaissance',
  cyber: 'Cyber-Tyranny',
};

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  handlers: Handlers;
}

export const CommandPaletteHost: React.FC<Props> = ({ open, onOpenChange, handlers }) => {
  const { theme, setTheme } = useTheme();

  const commands: CommandItem[] = React.useMemo(() => {
    const list: CommandItem[] = [
      // ── Workflow ───────────────────────────────────────────────────
      {
        id: 'workflow.new',
        label: 'New Workflow',
        group: 'Workflow',
        icon: FilePlus,
        onRun: handlers.newWorkflow,
      },
      {
        id: 'workflow.open',
        label: 'Open Workflow',
        group: 'Workflow',
        icon: FolderOpen,
        onRun: handlers.open,
      },
      {
        id: 'workflow.save',
        label: 'Save Workflow',
        group: 'Workflow',
        icon: Save,
        shortcut: '⌘S',
        onRun: handlers.save,
      },
      {
        id: 'workflow.export',
        label: 'Export Workflow',
        group: 'Workflow',
        icon: Download,
        onRun: handlers.exportFile,
      },
      {
        id: 'workflow.import',
        label: 'Import Workflow',
        group: 'Workflow',
        icon: Upload,
        onRun: handlers.importJSON,
      },

      // ── Run ────────────────────────────────────────────────────────
      handlers.isDeploying
        ? {
            id: 'run.stop',
            label: 'Stop Workflow',
            group: 'Run',
            icon: Square,
            onRun: handlers.stop,
          }
        : {
            id: 'run.start',
            label: 'Start Workflow',
            group: 'Run',
            icon: Play,
            onRun: handlers.run,
          },

      // ── Open panels ────────────────────────────────────────────────
      {
        id: 'open.settings',
        label: 'Open Settings',
        group: 'Open',
        icon: SettingsIcon,
        onRun: handlers.openSettings,
      },
      {
        id: 'open.credentials',
        label: 'Open Credentials',
        group: 'Open',
        icon: KeyRound,
        onRun: handlers.openCredentials,
      },

      // ── View toggles ───────────────────────────────────────────────
      {
        id: 'view.sidebar',
        label: 'Toggle Sidebar',
        group: 'View',
        icon: PanelLeftClose,
        onRun: handlers.toggleSidebar,
      },
      {
        id: 'view.palette',
        label: 'Toggle Component Palette',
        group: 'View',
        icon: PanelRightClose,
        onRun: handlers.toggleComponentPalette,
      },
      {
        id: 'view.console',
        label: 'Toggle Console / Chat Panel',
        group: 'View',
        icon: Terminal,
        onRun: handlers.toggleConsolePanel,
      },
    ];

    // ── Theme switch ─────────────────────────────────────────────────
    for (const name of AVAILABLE_THEMES) {
      list.push({
        id: `theme.${name}`,
        label: `Switch theme: ${THEME_LABEL[name]}`,
        group: 'Theme',
        icon: PaletteIcon,
        hint: name === theme ? 'active' : undefined,
        onRun: () => setTheme(name),
      });
    }

    return list;
  }, [handlers, theme, setTheme]);

  return <CommandPalette open={open} onOpenChange={onOpenChange} commands={commands} />;
};

export default CommandPaletteHost;
