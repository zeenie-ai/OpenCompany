/**
 * Ctrl/Cmd + Shift + D switches between Normal and Dev mode, from anywhere
 * (the chord inserts no text, so it is safe inside inputs). Ignored while an
 * IME is composing, and on key repeat.
 */

import { useEffect } from 'react';
import { featureFlags } from '../lib/featureFlags';
import { toggleShellMode } from './useShellActions';

/** The pressed letter, falling back to the physical key on layouts whose
 *  D key produces a non-Latin character. */
function letterOf(event: KeyboardEvent): string {
  if (event.key.length === 1 && /[a-z]/i.test(event.key)) return event.key.toLowerCase();
  return event.code === 'KeyD' ? 'd' : '';
}

export function isModeShortcut(event: KeyboardEvent): boolean {
  if (event.isComposing || event.keyCode === 229) return false;
  if (!(event.ctrlKey || event.metaKey) || !event.shiftKey || event.altKey) return false;
  return letterOf(event) === 'd';
}

export function useModeShortcut(): void {
  useEffect(() => {
    if (!featureFlags.normalMode) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isModeShortcut(event)) return;
      // Chrome binds the chord to "bookmark all tabs".
      event.preventDefault();
      if (event.repeat) return;
      void toggleShellMode();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
}
