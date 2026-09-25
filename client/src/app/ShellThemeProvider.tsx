/**
 * The app's ThemeProvider, with the shell's rule: Home (Normal mode) shows
 * the base theme of the chosen theme's family, light or dark, because Home
 * is designed for those two only. The editor (Dev mode) shows the chosen
 * theme itself, stylized or not. Switching modes moves between the two
 * without changing the choice. index.html's pre-paint script applies the
 * same rule to the first frame.
 */

import type { ReactNode } from 'react';
import { ThemeProvider } from '../contexts/ThemeContext';
import { useShellMode } from './ShellModeSwitch';

export function ShellThemeProvider({ children }: { children: ReactNode }) {
  const mode = useShellMode();
  return <ThemeProvider baseOnly={mode === 'normal'}>{children}</ThemeProvider>;
}

export default ShellThemeProvider;
