/**
 * useSound — React glue for the per-theme WebAudio engine.
 *
 * `useSoundSync()` mounts once at the Dashboard root. It:
 *   - mirrors the `soundEnabled` Zustand slice into `Sounds.setEnabled()`
 *   - reads `--sound-pack` from `:root` after every theme change and
 *     calls `Sounds.setPack(...)` so the active pack tracks the active
 *     theme without per-component wiring
 *   - installs a global mouseenter / touchstart delegate that fires
 *     `play('hover')` for any element matching the design-handoff hover
 *     selector list (`.btn`, `.action-btn`, `.row`, `.menu-pop-item`,
 *     `.wf-card`, `.comp`, `.cmdk-item`, `[data-sound-hover]`).
 *
 * `useSound()` is the lightweight handle every event handler uses:
 *
 *     const play = useSound();
 *     <button onClick={() => { play('click'); onSave(); }} />
 *
 * `withSound()` is the convenience wrap to spice an existing handler:
 *
 *     <Button onClick={withSound('click', onSave)} />
 *
 * Adding a new sound event: extend `SoundEvent` in lib/sound.ts, add
 * an entry per pack, and fire `play('<event>')` from the relevant
 * handler. No additional wiring here.
 */

import { useEffect } from 'react';
import { toast } from 'sonner';
import { useTheme } from '../contexts/ThemeContext';
import { useAppStore } from '../store/useAppStore';
import { Sounds, type SoundPackName, type SoundEvent } from '../lib/sound';

const VALID_PACKS: ReadonlySet<SoundPackName> = new Set([
  'none', 'parchment', 'marble', 'ink', 'clockwork', 'vibraphone',
  'terminal', 'scrap', 'crypt', 'bell', 'telex',
]);

/** Selector for the global hover delegate. Mirrors the upstream
 *  `app/sound.js` auto-capture list, modulo class additions from W15
 *  (`.action-btn`, `.wf-card`, `.comp`, `.cmdk-item`). */
const HOVER_SELECTOR =
  '.btn, .action-btn, .row, .menu-pop-item, .wf-card, .comp, .cmdk-item, [data-sound-hover]';

function readSoundPack(): SoundPackName {
  if (typeof document === 'undefined') return 'none';
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue('--sound-pack')
    .trim()
    .replace(/['"]/g, '');
  return VALID_PACKS.has(raw as SoundPackName) ? (raw as SoundPackName) : 'none';
}

/**
 * One-shot monkey-patch of `toast.success` / `toast.error` so every
 * call site fires the matching per-theme sound automatically. Sonner
 * exports `toast` as a singleton object with mutable methods, so
 * patching once at module load is safe. Guarded by a flag so React
 * 18+ Strict-Mode double-invocation of useSoundSync doesn't double-
 * wrap the methods.
 */
let toastPatched = false;
function patchToast(): void {
  if (toastPatched) return;
  toastPatched = true;
  const originalSuccess = toast.success;
  const originalError = toast.error;
  // `toast.success` / `toast.error` accept (message, options) and
  // return the toast id. We preserve the return type by deferring to
  // the original after firing the sound.
  toast.success = ((...args: Parameters<typeof originalSuccess>) => {
    Sounds.play('success');
    return originalSuccess.apply(toast, args);
  }) as typeof originalSuccess;
  toast.error = ((...args: Parameters<typeof originalError>) => {
    Sounds.play('error');
    return originalError.apply(toast, args);
  }) as typeof originalError;
}

/** Mount once at the Dashboard root. */
export function useSoundSync(): void {
  const { theme } = useTheme();
  const enabled = useAppStore((s) => s.soundEnabled);

  // Patch sonner's `toast.success` / `toast.error` so every call fires
  // the matching sound. Idempotent — safe under React Strict Mode.
  useEffect(() => {
    patchToast();
  }, []);

  useEffect(() => {
    Sounds.setEnabled(enabled);
  }, [enabled]);

  useEffect(() => {
    Sounds.setPack(readSoundPack());
  }, [theme]);

  // Global hover delegate. Capture-phase listener so it picks up
  // mouseenter on any matching surface without each component wiring
  // its own onMouseEnter handler. touchstart mirrors the same selector
  // list for mobile / hybrid devices.
  useEffect(() => {
    const handleHover = (event: Event) => {
      const target = event.target as Element | null;
      if (!target || typeof target.closest !== 'function') return;
      if (target.closest(HOVER_SELECTOR)) {
        Sounds.play('hover');
      }
    };
    // mouseenter doesn't bubble, so we use the capture-phase approach
    // via mouseover (which does bubble) plus a relatedTarget filter so
    // a single hover only fires once per crossing-into-element.
    const handleMouseOver = (event: MouseEvent) => {
      const target = event.target as Element | null;
      const related = event.relatedTarget as Element | null;
      if (!target || typeof target.closest !== 'function') return;
      const matched = target.closest(HOVER_SELECTOR);
      if (!matched) return;
      // Only fire on enter — if the previous mouse position was already
      // inside the same matched element, skip.
      if (related && matched.contains(related)) return;
      Sounds.play('hover');
    };
    // Wave 33: PASSIVE listener so the handler can never block scroll /
    // input dispatch. The handler doesn't call preventDefault, so passive
    // is safe. Bare `true` (capture-only) registers an ACTIVE listener,
    // which means the browser must wait for the handler to finish before
    // dispatching the next input event — on tab return, when a burst of
    // queued mouseover events fires while the mouse is over the canvas,
    // the active handler's closest() DOM-walks blocked first-click input
    // dispatch by 5-15ms. Passive removes that block.
    //
    // removeEventListener's options bag only consults `capture` for
    // matching (W3C spec — passive isn't part of the listener identity),
    // so the cleanup pair below uses the same shape but TypeScript's
    // EventListenerOptions doesn't include `passive` — pass `true` for
    // capture-only removal which matches both add registrations.
    document.addEventListener('mouseover', handleMouseOver, { capture: true, passive: true });
    document.addEventListener('touchstart', handleHover, { capture: true, passive: true });
    return () => {
      document.removeEventListener('mouseover', handleMouseOver, true);
      document.removeEventListener('touchstart', handleHover, true);
    };
  }, []);

  // One-shot AudioContext unlock on the first user gesture. Modern
  // browsers (Chrome / Safari) keep the AudioContext suspended until a
  // resume() call originates from a gesture handler — without this, the
  // first play() can land microseconds after the gesture frame and the
  // sound is silently dropped. `Sounds.unlock()` is idempotent so the
  // `{ once: true }` listeners cover the lifetime fine.
  useEffect(() => {
    const handler = () => { Sounds.unlock(); };
    const opts: AddEventListenerOptions = { once: true, capture: true, passive: true };
    window.addEventListener('pointerdown', handler, opts);
    window.addEventListener('keydown', handler, opts);
    window.addEventListener('touchstart', handler, opts);
    return () => {
      window.removeEventListener('pointerdown', handler, opts);
      window.removeEventListener('keydown', handler, opts);
      window.removeEventListener('touchstart', handler, opts);
    };
  }, []);
}

/** Play handle. Returns the same `Sounds.play` reference each render. */
export function useSound(): (event: SoundEvent) => void {
  return Sounds.play;
}

/** Wrap an existing onClick / onChange handler so the matching sound
 *  fires before the underlying handler runs. Sound events are non-
 *  blocking (fire-and-forget OscillatorNode). When the engine is
 *  disabled or the active pack is `none`, this is a no-op. */
export function withSound<E extends ((...args: any[]) => any) | undefined>(
  event: SoundEvent,
  handler: E,
): (...args: E extends (...args: infer P) => any ? P : never[]) => void {
  return ((...args: any[]) => {
    Sounds.play(event);
    handler?.(...args);
  }) as any;
}
