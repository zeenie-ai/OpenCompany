/* eslint-disable react-refresh/only-export-components -- canonical React Context pattern co-locates Provider + hooks/helpers in one file. */
/**
 * ThemeContext — owns the active visual theme.
 *
 * Themes are resolved entirely in CSS via `:root[data-theme="<name>"]`
 * scoped blocks under client/src/themes/. This provider's job is to
 * write the theme to `<html data-theme>` (and the legacy
 * `<html class="dark">` flag for any `dark:` Tailwind variants still
 * in use), persist the user's choice to localStorage, and migrate legacy
 * callers reading `isDarkMode` / `toggleTheme`.
 *
 * Two themes are tracked: the one the user chose (`chosenTheme`, persisted)
 * and the one on the page (`theme`). They differ only under `baseOnly`,
 * which shows a chosen stylized theme as its family's base theme (light or
 * dark) and keeps the choice for when it clears. Home sets it (see
 * app/ShellThemeProvider): Home is designed for the two base themes only.
 *
 * The DOM is written synchronously inside `setTheme`, before React state
 * changes, so any effect reacting to the new theme (the sound pack reads
 * `--sound-pack` from computed style) already sees the new tokens.
 * `index.html` runs a pre-paint copy of `loadInitialTheme` and the
 * `baseOnly` rule so the first frame is already themed; its theme lists
 * are locked to the ones below by `contexts/__tests__/themePrePaint.test.ts`.
 *
 * `setTheme(next, { reveal: true, origin })` animates the switch: the new
 * theme grows as a circle from `origin` over the old one (View Transitions),
 * falls back to a short colour fade where View Transitions are missing, and
 * is instant under prefers-reduced-motion.
 *
 * Adding a new theme: drop a new CSS file under client/src/themes/
 * scoped to `:root[data-theme="<name>"]`, import it in main.tsx, and
 * add the name to `AVAILABLE_THEMES` below and to the pre-paint script
 * in index.html.
 */

import React, { createContext, useCallback, useContext, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { BRAND_STORAGE_KEYS, readAndMigrateStorageValue } from '../lib/brandStorage';
import { animate, dur, invalidateMotionTokens } from '../lib/motion';
import { prefersReducedMotion } from '../lib/useReducedMotion';

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
export const DARK_FAMILY: ReadonlySet<ThemeName> = new Set<ThemeName>([
  'dark', 'cyber', 'wasteland', 'rot', 'surveillance', 'steampunk',
]);

export function isDarkTheme(theme: ThemeName): boolean {
  return DARK_FAMILY.has(theme);
}

/** The base theme of the other family: any dark-family theme maps to
 *  `light`, any light-family theme to `dark`. Drives the sun/moon toggle. */
export function oppositeBaseTheme(theme: ThemeName): ThemeName {
  return DARK_FAMILY.has(theme) ? 'light' : 'dark';
}

/** The base theme of the same family: `dark` for a dark-family theme,
 *  `light` otherwise. What `baseOnly` shows for a chosen theme. */
export function familyBaseTheme(theme: ThemeName): ThemeName {
  return DARK_FAMILY.has(theme) ? 'dark' : 'light';
}

export interface SetThemeOptions {
  /** Animate the switch (circular reveal, or a colour fade). */
  reveal?: boolean;
  /** Viewport point the reveal grows from. Default: the viewport centre. */
  origin?: { x: number; y: number };
}

interface ThemeContextType {
  /** The theme on the page (what `<html data-theme>` says). */
  theme: ThemeName;
  /** The theme the user chose. Differs from `theme` only under `baseOnly`. */
  chosenTheme: ThemeName;
  /** Choose a theme. Under `baseOnly` the page shows its family's base. */
  setTheme: (t: ThemeName, options?: SetThemeOptions) => void;
  availableThemes: readonly ThemeName[];
  /** True for every theme in `DARK_FAMILY`. */
  isDarkMode: boolean;
  /** Flip to the other family's base theme (see `oppositeBaseTheme`). */
  toggleTheme: (options?: SetThemeOptions) => void;
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

/** Write `theme` to `<html>`. Idempotent. Storage holds the chosen theme,
 *  which is not always the one on the page, so it is saved separately. */
export function applyThemeToDom(theme: ThemeName): void {
  const html = document.documentElement;
  if (html.dataset.theme !== theme) html.dataset.theme = theme;
  html.classList.toggle('dark', DARK_FAMILY.has(theme));
  invalidateMotionTokens();
}

function saveChosenTheme(theme: ThemeName): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEYS.canonical, theme);
  } catch {
    // Storage may be blocked by browser policy; the in-memory theme still applies.
  }
}

/** Disables CSS transitions while the new theme is captured, so it snaps. */
const SWAPPING_CLASS = 'oc-theme-swapping';
/** Enables short colour transitions everywhere (no View Transitions fallback). */
const FADE_CLASS = 'oc-theme-fade';

interface ViewTransitionLike {
  ready: Promise<void>;
  finished: Promise<void>;
}

type DocumentWithViewTransition = Document & {
  startViewTransition?: (update: () => void) => ViewTransitionLike;
};

/** Radius that covers the whole viewport from `origin`. */
function revealRadius(origin: { x: number; y: number }): number {
  const dx = Math.max(origin.x, window.innerWidth - origin.x);
  const dy = Math.max(origin.y, window.innerHeight - origin.y);
  return Math.hypot(dx, dy);
}

let fadeTimer: number | undefined;

interface ThemeProviderProps {
  children: React.ReactNode;
  /** Show only the base themes: a chosen stylized theme appears as its
   *  family's base (`familyBaseTheme`). The choice itself is kept and
   *  shows again once this clears. */
  baseOnly?: boolean;
}

export const ThemeProvider: React.FC<ThemeProviderProps> = ({ children, baseOnly = false }) => {
  const [chosen, setChosen] = useState<ThemeName>(loadInitialTheme);
  const shown = baseOnly ? familyBaseTheme(chosen) : chosen;
  // The most recently requested theme. A revealed switch commits React state
  // only inside the View Transition callback, so a second click before then
  // must toggle from the pending theme, not the rendered one.
  const requestedRef = useRef<ThemeName>(chosen);
  const baseOnlyRef = useRef(baseOnly);

  useLayoutEffect(() => {
    baseOnlyRef.current = baseOnly;
  }, [baseOnly]);

  // Safety net for the first render (no pre-paint script, e.g. tests), and
  // the path for `baseOnly` changing (a mode switch). Layout effects run
  // before every passive effect, so consumers still see the DOM already
  // switched.
  useLayoutEffect(() => {
    applyThemeToDom(shown);
  }, [shown]);

  useLayoutEffect(() => {
    saveChosenTheme(chosen);
  }, [chosen]);

  const setTheme = useCallback((next: ThemeName, options?: SetThemeOptions) => {
    requestedRef.current = next;
    const html = document.documentElement;
    const onPage = (theme: ThemeName) => (baseOnlyRef.current ? familyBaseTheme(theme) : theme);
    const commit = () => {
      applyThemeToDom(onPage(next));
      setChosen(next);
    };

    if (!options?.reveal || prefersReducedMotion() || html.dataset.theme === onPage(next)) {
      commit();
      return;
    }

    const doc = document as DocumentWithViewTransition;
    if (typeof doc.startViewTransition !== 'function') {
      html.classList.add(FADE_CLASS);
      commit();
      window.clearTimeout(fadeTimer);
      fadeTimer = window.setTimeout(() => html.classList.remove(FADE_CLASS), dur('theme-fade'));
      return;
    }

    const origin = options.origin ?? { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    html.classList.add(SWAPPING_CLASS);
    let transition: ViewTransitionLike;
    try {
      // The browser runs this after capturing the old frame, which can be
      // after a newer switch; applying the latest request (not `next`)
      // keeps a stale callback from overriding it.
      transition = doc.startViewTransition(() => {
        const latest = requestedRef.current;
        applyThemeToDom(onPage(latest));
        flushSync(() => setChosen(latest));
      });
    } catch {
      html.classList.remove(SWAPPING_CLASS);
      commit();
      return;
    }

    transition.ready
      .then(() => {
        const radius = revealRadius(origin);
        const at = `at ${origin.x}px ${origin.y}px`;
        animate(
          html,
          { clipPath: [`circle(0px ${at})`, `circle(${radius}px ${at})`] },
          { duration: 'theme-reveal', easing: 'reveal', pseudoElement: '::view-transition-new(root)' },
        );
        animate(
          html,
          { transform: ['scale(1)', 'scale(0.985)'], opacity: [1, 0.6] },
          { duration: 'theme-reveal', easing: 'reveal', pseudoElement: '::view-transition-old(root)' },
        );
      })
      .catch(() => {
        // Skipped (another switch started, or the document was hidden). The
        // update callback still runs, so the theme is applied regardless.
      });
    transition.finished
      .catch(() => {})
      .finally(() => html.classList.remove(SWAPPING_CLASS));
  }, []);

  const toggleTheme = useCallback(
    (options?: SetThemeOptions) => {
      setTheme(oppositeBaseTheme(requestedRef.current), options);
    },
    [setTheme],
  );

  const value = useMemo<ThemeContextType>(
    () => ({
      theme: shown,
      chosenTheme: chosen,
      setTheme,
      availableThemes: AVAILABLE_THEMES,
      isDarkMode: DARK_FAMILY.has(shown),
      toggleTheme,
    }),
    [shown, chosen, setTheme, toggleTheme],
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
