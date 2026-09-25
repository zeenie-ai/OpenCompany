/**
 * Switching between Normal mode (Home) and Dev mode (the workflow editor).
 * Every mode switch in the UI goes through these, never `setShellMode`:
 * they guard unsaved editor changes, preload the editor chunk, and run the
 * transition.
 *
 * Unsaved changes: the editor keeps its working copy in the app store, so
 * leaving for Home loses nothing. Opening a different workflow would replace
 * it, so `enterDev` saves it first when the auto-save preference is on (the
 * default) and asks otherwise.
 */

import { useMemo } from 'react';
import { toast } from 'sonner';
import { SPIKE, spikeOrb } from '../features/home/orb/orb';
import { featureFlags } from '../lib/featureFlags';
import { useAppStore } from '../store/useAppStore';
import { useWorkflowSettingsStore } from '../stores/workflowSettingsStore';
import { preloadEditor, preloadHome } from './ShellModeSwitch';
import { transitionShell } from './shellTransition';

export interface EnterDevOptions {
  /** Open this workflow in the editor (an employee's "Open workflow"). */
  workflowId?: string;
}

/** Keep, save, or refuse the editor's unsaved work before it is replaced.
 *  False means the switch must not happen. */
async function settleUnsavedWork(nextWorkflowId: string): Promise<boolean> {
  const { currentWorkflow, hasUnsavedChanges, saveWorkflow } = useAppStore.getState();
  if (!hasUnsavedChanges || !currentWorkflow || currentWorkflow.id === nextWorkflowId) return true;
  const autoSave = useWorkflowSettingsStore.getState().settings.autoSave;
  if (!autoSave && !window.confirm(`Save your changes to "${currentWorkflow.name}" before opening another workflow?`)) {
    return false;
  }
  try {
    await saveWorkflow();
  } catch (error) {
    console.error('[Shell] Failed to save before switching workflows:', error);
  }
  // saveWorkflow reports failure by leaving the changes marked unsaved.
  if (useAppStore.getState().hasUnsavedChanges) {
    toast.error(`Could not save "${currentWorkflow.name}". Your changes are still open in the editor.`);
    return false;
  }
  return true;
}

export async function enterDev(options: EnterDevOptions = {}): Promise<void> {
  if (!featureFlags.normalMode) return;
  const { workflowId } = options;
  if (workflowId && !(await settleUnsavedWork(workflowId))) return;
  const opening = workflowId && useAppStore.getState().currentWorkflow?.id !== workflowId
    ? useAppStore.getState().loadWorkflow(workflowId)
    : Promise.resolve();
  try {
    await Promise.all([opening, preloadEditor()]);
  } catch (error) {
    console.error('[Shell] Could not open the editor:', error);
    toast.error('Could not open the workflow. Check the connection and try again.');
    return;
  }
  await transitionShell('dev');
}

export async function enterNormal(): Promise<void> {
  if (!featureFlags.normalMode) return;
  // Loaded before the swap, so Home's entrance never plays on a placeholder.
  try {
    await preloadHome();
  } catch (error) {
    console.error('[Shell] Could not load Home:', error);
  }
  await transitionShell('normal');
  // Home's orb flares as it arrives (it fades out within a second).
  spikeOrb(SPIKE.mode);
}

export function toggleShellMode(): Promise<void> {
  return useAppStore.getState().shellMode === 'dev' ? enterNormal() : enterDev();
}

export function useShellActions() {
  return useMemo(() => ({ enterDev, enterNormal, toggleShellMode }), []);
}
