/**
 * useAppTheme — canvas + map theme accessor.
 *
 * Returns a flat colour pack with the same shape the canvas + map +
 * grandfathered-modal call sites expect (`theme.colors.X`,
 * `theme.isDarkMode`): `lightColors` or `darkColors`, picked by the
 * active theme's family (`DARK_FAMILY`). Canvas EDGE strokes are
 * no longer part of this pack — the design-handoff edge migration
 * moved them to CSS tokens (--edge-stroke + semantic status recolors
 * in styles/canvasAnimations.ts); do not reintroduce edge* keys here.
 */

import { useMemo } from 'react';
import { useTheme } from '../contexts/ThemeContext';
import { theme as baseTheme, lightColors, darkColors } from '../styles/theme';

export const useAppTheme = () => {
  const { isDarkMode } = useTheme();

  return useMemo(() => ({
    ...baseTheme,
    colors: isDarkMode ? darkColors : lightColors,
    isDarkMode,
  }), [isDarkMode]);
};
