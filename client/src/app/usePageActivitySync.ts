/**
 * Keeps `<html data-page-hidden>` and the `pageActivity` store in step with
 * whether anyone can see the app.
 *
 * Why the attribute exists (Wave 33): browsers keep advancing CSS keyframe
 * timing on hidden tabs, and when the tab returns every paused frame of
 * every executing node's three-layer `node-pulse` (plus Cyber's
 * full-viewport flicker/roll) flushes at once. The compositor stalls for
 * 100-200 ms and the first click after returning is swallowed. base.css
 * pauses every CSS animation while the attribute is present. Script-driven
 * motion (lib/motion, the Home orb) reads `pageActivity` for the same signal.
 *
 * "Not visible" is the tab being hidden OR the window being blurred:
 * `visibilitychange` alone misses window-minimize on Windows and app
 * switching on macOS, which do fire blur/focus. On resume the attribute is
 * removed two frames later, so the first input dispatches before the
 * composite resumes.
 *
 * Moved from Dashboard so it runs on both screens, with two fixes: the
 * resume frames are cancelled on unmount and on every new signal (they
 * leaked and could fire after a later hide), and unmounting clears the
 * attribute (it could stay set and freeze every CSS animation).
 */

import { useEffect } from 'react';
import { pageActivity } from '../lib/pageActivity';

export function usePageActivitySync(): void {
  useEffect(() => {
    const root = document.documentElement;
    let blurred = false;
    let outer = 0;
    let inner = 0;

    const cancelResume = () => {
      if (outer) cancelAnimationFrame(outer);
      if (inner) cancelAnimationFrame(inner);
      outer = 0;
      inner = 0;
    };

    const apply = () => {
      cancelResume();
      if (document.hidden || blurred) {
        root.setAttribute('data-page-hidden', '');
        pageActivity.set(false);
        return;
      }
      outer = requestAnimationFrame(() => {
        outer = 0;
        inner = requestAnimationFrame(() => {
          inner = 0;
          if (document.hidden || blurred) return;
          root.removeAttribute('data-page-hidden');
          pageActivity.set(true);
        });
      });
    };

    const onVisibility = () => apply();
    const onBlur = () => {
      blurred = true;
      apply();
    };
    const onFocus = () => {
      blurred = false;
      apply();
    };

    // A page opened in a background tab starts hidden.
    if (document.hidden) apply();
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('blur', onBlur);
    window.addEventListener('focus', onFocus);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('blur', onBlur);
      window.removeEventListener('focus', onFocus);
      cancelResume();
      root.removeAttribute('data-page-hidden');
      pageActivity.set(true);
    };
  }, []);
}
