/* eslint-disable react-refresh/only-export-components -- canonical React Context pattern co-locates Provider + hooks/helpers in one file. */
/**
 * ThemeContext — owns the active visual theme.
 *
 * Themes are resolved entirely in CSS via `:root[data-theme="<name>"]`
 * scoped blocks under client/src/themes/. This provider's only job is
 * to write the chosen name to `<html data-theme>` (and the legacy
 * `<html class="dark">` flag for any `dark:` Tailwind variants still
 * in use), persist the choice to localStorage, and migrate legacy
 * callers reading `isDarkMode` / `toggleTheme`.
 *
 * Adding a new theme: drop a new CSS file under client/src/themes/
 * scoped to `:root[data-theme="<name>"]`, import it in main.tsx, and
 * add the name to `AVAILABLE_THEMES` below.
 */

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { BRAND_STORAGE_KEYS, readAndMigrateStorageValue } from '../lib/brandStorage';

export type ThemeName =
  | 'light' | 'dark'
  | 'renaissance' | 'greek' | 'edo' | 'steampunk' | 'atomic'
  | 'cyber' | 'wasteland' | 'rot' | 'plague' | 'surveillance';

/** Order matters — drives the ThemeSwitcher menu and keyboard rotation.
 *  Utopian set first (light + dark + 5 utopian), dystopian set after. */
export const AVAILABLE_THEMES: readonly ThemeName[] = [
  'light', 'dark',
  'renaissance', 'greek', 'edo', 'steampunk', 'atomic',
  'cyber', 'wasteland', 'rot', 'plague', 'surveillance',
];

const THEME_STORAGE_KEYS = BRAND_STORAGE_KEYS.theme;
const LEGACY_DARK_MODE_KEY = 'darkMode';

/** Themes whose backgrounds are dark — also flip the legacy `.dark`
 *  Tailwind variant so existing `dark:` utilities resolve correctly. */
const DARK_FAMILY: ReadonlySet<ThemeName> = new Set([
  'dark', 'cyber', 'wasteland', 'rot', 'surveillance', 'steampunk',
]);

interface ThemeContextType {
  theme: ThemeName;
  setTheme: (t: ThemeName) => void;
  availableThemes: readonly ThemeName[];
  /** Backwards-compat: true for dark + cyber. */
  isDarkMode: boolean;
  /** Backwards-compat: rotates light <-> dark only (preserves legacy callers). */
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

function isThemeName(value: string | null | undefined): value is ThemeName {
  return !!value && (AVAILABLE_THEMES as readonly string[]).includes(value);
}

function loadInitialTheme(): ThemeName {
  // 1. Honour the OpenCompany key first, migrating the pre-rebrand key once.
  const stored = readAndMigrateStorageValue(localStorage, THEME_STORAGE_KEYS);
  if (isThemeName(stored)) return stored;

  // 2. Migrate the legacy `darkMode` boolean key (was 'true' / 'false' / null).
  const legacy = localStorage.getItem(LEGACY_DARK_MODE_KEY);
  if (legacy !== null) {
    const migrated: ThemeName = legacy === 'false' ? 'light' : 'dark';
    localStorage.setItem(THEME_STORAGE_KEYS.canonical, migrated);
    localStorage.removeItem(LEGACY_DARK_MODE_KEY);
    return migrated;
  }

  // 3. First-launch default — match the previous default of `isDarkMode: true`.
  return 'dark';
}

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [theme, setThemeState] = useState<ThemeName>(loadInitialTheme);

  useEffect(() => {
    const html = document.documentElement;
    html.dataset.theme = theme;
    if (DARK_FAMILY.has(theme)) {
      html.classList.add('dark');
    } else {
      html.classList.remove('dark');
    }
    localStorage.setItem(THEME_STORAGE_KEYS.canonical, theme);
  }, [theme]);

  const setTheme = useCallback((next: ThemeName) => {
    setThemeState(next);
  }, []);

  const toggleTheme = useCallback(() => {
    // Legacy semantics: bounce between light and dark only. Renaissance /
    // Cyber are reachable through `setTheme` / `<ThemeSwitcher>` instead.
    setThemeState((prev) => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  const value = useMemo<ThemeContextType>(
    () => ({
      theme,
      setTheme,
      availableThemes: AVAILABLE_THEMES,
      isDarkMode: DARK_FAMILY.has(theme),
      toggleTheme,
    }),
    [theme, setTheme, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};
