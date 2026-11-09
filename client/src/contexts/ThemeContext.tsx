/* eslint-disable react-refresh/only-export-components -- canonical React Context pattern co-locates Provider + hooks/helpers in one file. */
/**
 * ThemeContext — owns the active visual theme.
 *
 * Themes are resolved entirely in CSS via `:root[data-theme="<name>"]`
 * scoped blocks under client/src/themes/. This provider's job is to
 * write the theme to `<html data-theme>` (and the legacy
 * `<html class="dark">` flag for any `dark:` Tailwind variants still
 * in use), persist the user's choice to localStorage, and migrate legacy
 * callers reading `isDarkMode`. Home, the editor and the sign-in gate all
 * show the chosen theme; the theme button steps through the themes with
 * `cycleTheme`.
 *
 * The DOM is written synchronously inside `setTheme`, before React state
 * changes, so any effect reacting to the new theme (the sound pack reads
 * `--sound-pack` from computed style) already sees the new tokens.
 * `index.html` runs a pre-paint copy of `loadInitialTheme` so the first
 * frame is already themed; its theme lists are locked to the ones below by
 * `contexts/__tests__/themePrePaint.test.ts`.
 *
 * `setTheme(next, { reveal: true, origin })` animates the switch: the new
 * theme grows as a circle from `origin` over the old one (View Transitions),
 * falls back to a short colour fade where View Transitions are missing, and
 * is instant under prefers-reduced-motion.
 *
 * A theme chosen in another window or tab (the `storage` event on the theme
 * key) shows here too, at once and without being saved again, so two
 * windows never echo each other. `applyThemeToDom` also keeps a
 * `<meta name="theme-color">` at the theme's `--background`, the colour the
 * desktop shell paints its window with.
 *
 * Adding a new theme: drop a new CSS file under client/src/themes/
 * scoped to `:root[data-theme="<name>"]`, import it in main.tsx, and
 * add the name to `AVAILABLE_THEMES` and `THEME_LABEL` below and to the
 * pre-paint script in index.html.
 */

import React, { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { BRAND_STORAGE_KEYS, readAndMigrateStorageValue } from '../lib/brandStorage';
import { animate, dur, invalidateMotionTokens } from '../lib/motion';
import { prefersReducedMotion } from '../lib/useReducedMotion';

export type ThemeName = 'light' | 'dark';

/** Order matters: the theme button steps through it in this order, and it
 *  is the order of Settings → Appearance and the command palette. */
export const AVAILABLE_THEMES: readonly ThemeName[] = ['light', 'dark'];

/** Each theme's name, wherever one is shown. */
export const THEME_LABEL: Record<ThemeName, string> = {
  light: 'Light',
  dark: 'Dark',
};

const THEME_STORAGE_KEYS = BRAND_STORAGE_KEYS.theme;
const LEGACY_DARK_MODE_KEY = 'darkMode';

/** Themes whose backgrounds are dark — also flip the legacy `.dark`
 *  Tailwind variant so existing `dark:` utilities resolve correctly. */
export const DARK_FAMILY: ReadonlySet<ThemeName> = new Set<ThemeName>(['dark']);

/** The theme after `theme` in `AVAILABLE_THEMES`, wrapping round. */
export function nextTheme(theme: ThemeName): ThemeName {
  return AVAILABLE_THEMES[(AVAILABLE_THEMES.indexOf(theme) + 1) % AVAILABLE_THEMES.length];
}

export interface SetThemeOptions {
  /** Animate the switch (circular reveal, or a colour fade). */
  reveal?: boolean;
  /** Viewport point the reveal grows from. Default: the viewport centre. */
  origin?: { x: number; y: number };
}

interface ThemeContextType {
  /** The chosen theme, which is the one on the page (`<html data-theme>`). */
  theme: ThemeName;
  setTheme: (t: ThemeName, options?: SetThemeOptions) => void;
  availableThemes: readonly ThemeName[];
  /** True for every theme in `DARK_FAMILY`. */
  isDarkMode: boolean;
  /** Switch to the next theme (see `nextTheme`). */
  cycleTheme: (options?: SetThemeOptions) => void;
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

/** Write `theme` to `<html>`, and its background colour to the page's
 *  `theme-color` meta. Idempotent. */
export function applyThemeToDom(theme: ThemeName): void {
  const html = document.documentElement;
  if (html.dataset.theme !== theme) html.dataset.theme = theme;
  html.classList.toggle('dark', DARK_FAMILY.has(theme));
  invalidateMotionTokens();

  let meta = document.head.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement('meta');
    meta.name = 'theme-color';
    document.head.append(meta);
  }
  // Empty only where no theme CSS is loaded (tests).
  const background = getComputedStyle(html).getPropertyValue('--background').trim();
  if (background) meta.content = background;
}

function saveTheme(theme: ThemeName): void {
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

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [theme, setThemeState] = useState<ThemeName>(loadInitialTheme);
  // The most recently requested theme. A revealed switch commits React state
  // only inside the View Transition callback, so a second click before then
  // must step on from the pending theme, not the rendered one.
  const requestedRef = useRef<ThemeName>(theme);
  // The theme storage holds as far as this window knows: the last one it
  // saved, or one another window saved. Only a theme that differs is saved,
  // so a theme from another window is never written back.
  const storedRef = useRef<ThemeName | null>(null);

  // Safety net for the first render (no pre-paint script, e.g. tests), and
  // the save of every choice made in this window. Layout effects run before
  // every passive effect, so consumers still see the DOM already switched.
  useLayoutEffect(() => {
    applyThemeToDom(theme);
    if (storedRef.current !== theme) saveTheme(theme);
    storedRef.current = theme;
  }, [theme]);

  const setTheme = useCallback((next: ThemeName, options?: SetThemeOptions) => {
    requestedRef.current = next;
    const html = document.documentElement;
    const commit = () => {
      applyThemeToDom(next);
      setThemeState(next);
    };

    if (!options?.reveal || prefersReducedMotion() || html.dataset.theme === next) {
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
        applyThemeToDom(latest);
        flushSync(() => setThemeState(latest));
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

  const cycleTheme = useCallback(
    (options?: SetThemeOptions) => {
      setTheme(nextTheme(requestedRef.current), options);
    },
    [setTheme],
  );

  // Another window or tab saved a theme: show it here at once. Going through
  // setTheme moves the requested theme too, so a later cycleTheme steps on
  // from it and a pending revealed switch cannot override it.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      const next = event.newValue;
      if (event.key !== THEME_STORAGE_KEYS.canonical || !isThemeName(next) || next === requestedRef.current) return;
      storedRef.current = next;
      setTheme(next);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [setTheme]);

  const value = useMemo<ThemeContextType>(
    () => ({
      theme,
      setTheme,
      availableThemes: AVAILABLE_THEMES,
      isDarkMode: DARK_FAMILY.has(theme),
      cycleTheme,
    }),
    [theme, setTheme, cycleTheme],
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
