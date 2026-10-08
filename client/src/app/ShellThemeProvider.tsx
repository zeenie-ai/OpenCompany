/**
 * The app's ThemeProvider, with the shell's rule: Home (Normal mode) and
 * the sign-in gate's screens (Connecting, sign-in) show the base theme of
 * the chosen theme's family, light or dark, because they are designed for
 * those two only. The editor (Dev mode) shows the chosen theme itself,
 * stylized or not. Moving between them never changes the choice.
 * index.html's pre-paint script applies the mode rule to the first frame.
 */

import type { ReactNode } from 'react';
import { ThemeProvider } from '../contexts/ThemeContext';
import { useShellDialogsStore } from '../stores/shellDialogsStore';
import { useShellMode } from './ShellModeSwitch';

export function ShellThemeProvider({ children }: { children: ReactNode }) {
  const mode = useShellMode();
  const connectScreen = useShellDialogsStore((s) => s.connectScreenOpen);
  return <ThemeProvider baseOnly={mode === 'normal' || connectScreen}>{children}</ThemeProvider>;
}

export default ShellThemeProvider;
