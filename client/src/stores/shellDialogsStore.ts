/**
 * App-level dialogs owned by the shell (app/AppShell), so either screen can
 * open them: the editor's toolbar and command palette, and Normal mode.
 * Screen-local dialogs (node context menu, result modal) stay in their
 * screen.
 */

import { create } from 'zustand';

export type CredentialsIntent = 'connect' | 'manage';

export interface CredentialsOptions {
  providerId?: string;
  categoryId?: string;
  intent?: CredentialsIntent;
}

interface ShellDialogsState {
  settingsOpen: boolean;
  credentialsOpen: boolean;
  credentialsOptions: CredentialsOptions & { intent: CredentialsIntent };
  /** Repeated opens are navigation requests, even when their target matches. */
  credentialsRequestId: number;
  /** The sign-in gate's Connecting or sign-in screen is showing
   *  (components/auth/ConnectScreen): the theme shows as its base. */
  connectScreenOpen: boolean;
  setConnectScreenOpen: (open: boolean) => void;
  openSettings: () => void;
  closeSettings: () => void;
  openCredentials: (options?: CredentialsOptions) => void;
  closeCredentials: () => void;
}

export const useShellDialogsStore = create<ShellDialogsState>((set) => ({
  settingsOpen: false,
  credentialsOpen: false,
  credentialsOptions: { intent: 'manage' },
  credentialsRequestId: 0,
  connectScreenOpen: false,
  setConnectScreenOpen: (open) => set({ connectScreenOpen: open }),
  openSettings: () => set({ settingsOpen: true }),
  closeSettings: () => set({ settingsOpen: false }),
  openCredentials: (options) => set((state) => ({
    credentialsOpen: true,
    credentialsOptions: { ...options, intent: options?.intent ?? 'manage' },
    credentialsRequestId: state.credentialsRequestId + 1,
  })),
  closeCredentials: () => set({ credentialsOpen: false }),
}));
