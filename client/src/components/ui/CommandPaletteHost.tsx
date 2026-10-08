/* eslint-disable react-refresh/only-export-components -- lifecycle command builder is exported for focused capability tests. */
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
  Pause,
  RotateCcw,
  FilePlus,
  FolderOpen,
  PanelLeftClose,
  PanelRightClose,
  Terminal,
  MessageSquare,
  Palette as PaletteIcon,
  Download,
  Upload,
} from 'lucide-react';
import { CommandPalette, type CommandItem } from './CommandPalette';
import { AVAILABLE_THEMES, useTheme, type ThemeName } from '../../contexts/ThemeContext';
import {
  type WorkflowControlPendingMutation,
  type WorkflowControlStatus,
} from '../../contexts/WebSocketContext';

export interface CommandPaletteHandlers {
  save: () => void;
  newWorkflow: () => void;
  open: () => void;
  start: () => void;
  pause: () => void;
  resume: () => void;
  reset: () => void;
  workflowControl: WorkflowControlStatus;
  workflowControlPending?: WorkflowControlPendingMutation;
  exportFile: () => void;
  importJSON: () => void;
  openSettings: () => void;
  openCredentials: () => void;
  toggleSidebar: () => void;
  toggleComponentPalette: () => void;
  toggleConsolePanel: () => void;
  /** Open the console's Chat and put the cursor in its message box (the
   *  editor's way to what ⌘K does on Home, where ⌘K is the palette). */
  focusChat?: () => void;
}

const THEME_LABEL: Record<ThemeName, string> = {
  light:        'Light',
  dark:         'Dark',
  renaissance:  'Renaissance',
  greek:        'Greek',
  edo:          'Edo',
  steampunk:    'Steampunk',
  atomic:       'Atomic Modern',
  cyber:        'Cyber-Tyranny',
  wasteland:    'Wasteland',
  rot:          'Necromantic Rot',
  plague:       'Plague City',
  surveillance: 'Surveillance',
};

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  handlers: CommandPaletteHandlers;
}

export const buildWorkflowLifecycleCommands = (
  handlers: Pick<
    CommandPaletteHandlers,
    'start' | 'pause' | 'resume' | 'reset' | 'workflowControl' | 'workflowControlPending'
  >,
): CommandItem[] => {
  if (handlers.workflowControlPending) {
    return [];
  }

  const { state } = handlers.workflowControl;
  const commands: CommandItem[] = [];
  if (handlers.workflowControl.can_start) {
    commands.push({
      id: 'run.start',
      label: 'Start Workflow',
      group: 'Run',
      icon: Play,
      onRun: handlers.start,
    });
  }
  if (handlers.workflowControl.can_pause || state === 'pausing') {
    commands.push({
      id: 'run.pause',
      label: state === 'pausing' ? 'Stopping Workflow…' : 'Stop Workflow',
      group: 'Run',
      icon: Pause,
      onRun: handlers.pause,
    });
  }
  if (handlers.workflowControl.can_resume || state === 'resuming') {
    commands.push({
      id: 'run.resume',
      label: state === 'resuming' ? 'Retry Resume Workflow' : 'Resume Workflow',
      group: 'Run',
      icon: Play,
      onRun: handlers.resume,
    });
  }
  if (handlers.workflowControl.can_reset) {
    commands.push({
      id: 'run.reset',
      label: state === 'resetting'
        ? 'Retry Reset Workflow Execution'
        : 'Reset Workflow Execution',
      group: 'Run',
      icon: RotateCcw,
      hint: 'terminates active work',
      onRun: handlers.reset,
    });
  }
  return commands;
};

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
      ...buildWorkflowLifecycleCommands(handlers),

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
      ...(handlers.focusChat
        ? [{ id: 'open.chat', label: 'Focus Chat', group: 'Open', icon: MessageSquare, onRun: handlers.focusChat }]
        : []),

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
