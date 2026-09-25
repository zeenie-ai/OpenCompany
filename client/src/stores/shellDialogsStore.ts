/**
 * App-level dialogs owned by the shell (app/AppShell), so either screen can
 * open them: the editor's toolbar and command palette, and Normal mode.
 * Screen-local dialogs (node context menu, result modal) stay in their
 * screen.
 */

import { create } from 'zustand';

interface ShellDialogsState {
  settingsOpen: boolean;
  credentialsOpen: boolean;
  /** Increments to ask the (editor-only) onboarding wizard to reopen. */
  onboardingReplay: number;
  openSettings: () => void;
  closeSettings: () => void;
  openCredentials: () => void;
  closeCredentials: () => void;
  replayOnboarding: () => void;
}

export const useShellDialogsStore = create<ShellDialogsState>((set) => ({
  settingsOpen: false,
  credentialsOpen: false,
  onboardingReplay: 0,
  openSettings: () => set({ settingsOpen: true }),
  closeSettings: () => set({ settingsOpen: false }),
  openCredentials: () => set({ credentialsOpen: true }),
  closeCredentials: () => set({ credentialsOpen: false }),
  replayOnboarding: () => set((state) => ({ settingsOpen: false, onboardingReplay: state.onboardingReplay + 1 })),
}));
