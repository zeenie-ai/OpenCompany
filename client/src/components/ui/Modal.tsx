/**
 * Modal — composition primitive on top of shadcn Dialog.
 *
 * Owns the recurring "title bar with centered headerActions and a close
 * button + size-constrained content panel" layout that 8 call sites
 * share. Not a facade preserving an old library's API — the call sites
 * use this because the composition is genuinely reused. Don't add new
 * panels by re-implementing this with raw <Dialog>; extend Modal here
 * if you need a new prop.
 */

import React, { useEffect, useRef } from 'react';
import { X, Settings } from 'lucide-react';
import {
  Dialog,
  DialogPortal,
  DialogOverlay,
  DialogTitle,
  DialogDescription,
  DialogClose,
} from '@/components/ui/dialog';
import { Dialog as DialogPrimitive } from 'radix-ui';
import { cn } from '@/lib/utils';
import { Sounds } from '@/lib/sound';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  children: React.ReactNode;
  title?: string;
  maxWidth?: string;
  maxHeight?: string;
  headerActions?: React.ReactNode;
  /** When true, modal height fits content up to maxHeight instead of fixed at maxHeight. */
  autoHeight?: boolean;
  /**
   * Body scroll behavior. Default `true` — the body renders an
   * `overflow-y-auto` wrapper for simple short content (alerts,
   * onboarding, pricing config). Set `false` for layouts that declare
   * their own internal scroll regions (parameter panel) so the body
   * uses `overflow-hidden flex-col` instead and there is a single
   * stable scroll context. Without this opt-out, tall accordion
   * content (e.g. Connected Skills under the AI agent panel) gets
   * clipped between the outer body scroller and the inner
   * `flex h-full min-h-0` chain.
   */
  scrollableBody?: boolean;
  /** Optional extra classes for the content panel. */
  className?: string;
}

const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  children,
  title,
  maxWidth = '500px',
  maxHeight = '80vh',
  headerActions,
  autoHeight = false,
  scrollableBody = true,
  className,
}) => {
  const showHeader = Boolean(title || headerActions);

  // Fire per-theme open/close sounds on isOpen transitions only — not
  // on first mount (use a previous-value ref to detect the actual
  // edge). Sounds.play is a no-op when the engine is disabled.
  const prevOpenRef = useRef(isOpen);
  useEffect(() => {
    const prev = prevOpenRef.current;
    if (prev !== isOpen) {
      Sounds.play(isOpen ? 'modalOpen' : 'modalClose');
      prevOpenRef.current = isOpen;
    }
  }, [isOpen]);

  return (
    <Dialog open={isOpen} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogPortal>
        {/* bg-bg-overlay reads --bg-overlay (each theme owns its own
            scrim alpha + tone — Renaissance uses ink-brown, Cyber uses
            void-near-black, light/dark use plain blacks). */}
        <DialogOverlay className="bg-bg-overlay supports-backdrop-filter:backdrop-blur-xs" />
        <DialogPrimitive.Content
          data-slot="dialog-content"
          className={cn(
            // bg-bg-app + border-border-default consume the new-contract
            // tokens directly so modals inherit the surface hierarchy
            // defined by the active theme (parchment under Renaissance,
            // void under Cyber).
            //
            // `modal` + `modal-frame` are the design-handoff structural
            // hooks — per-theme CSS targets these classes for nailed-up
            // borders (Plague), gilded corners (Renaissance), neon
            // scanlines (Cyber), double-rule frames (Greek), parchment
            // textures (Renaissance) on the modal content.
            'modal modal-frame',
            'fixed top-1/2 left-1/2 z-50 flex -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg border border-border-default bg-bg-app shadow-2xl outline-none',
            'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 duration-100',
            className
          )}
          style={{
            width: maxWidth,
            minWidth: maxWidth,
            height: autoHeight ? 'auto' : maxHeight,
            maxHeight,
          }}
        >
          {showHeader ? (
            // Header: bg-bg-panel sits one elevation step above bg-bg-app
            // (panel surface above page surface). font-display + tracking
            // + text-transform are theme-driven so titles read as Cinzel
            // uppercase under Renaissance and Major Mono Display under
            // Cyber, while staying clean sans-serif under light/dark.
            <div className="modal-head relative flex w-full items-center border-b border-border-default bg-bg-panel px-5 py-3">
              <DialogTitle className="absolute left-5 flex items-center gap-2 font-display text-base font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
                <Settings className="h-4 w-4 opacity-70" />
                {title}
              </DialogTitle>
              <div className="flex flex-1 items-center justify-center">{headerActions}</div>
              <DialogClose
                onClick={onClose}
                className="absolute right-5 inline-flex h-8 w-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-hover hover:text-fg-default"
                aria-label="Close"
              >
                <X className="h-[18px] w-[18px]" />
              </DialogClose>
            </div>
          ) : (
            <DialogTitle className="sr-only">{title || 'Dialog'}</DialogTitle>
          )}
          <DialogDescription className="sr-only">{title || 'Modal dialog'}</DialogDescription>
          {/* Body. Default `scrollableBody` keeps the legacy
              `overflow-y-auto` wrapper for short-content callers
              (alerts, onboarding, pricing config). Layouts that declare
              their own internal scroll regions (parameter panel, etc.)
              opt out so the body uses `overflow-hidden flex-col` and
              the inner `flex h-full min-h-0` chain owns a single,
              stable scroll context — this is what lets tall accordion
              content (Connected Skills) scroll inside the parameter
              panel without getting clipped. */}
          <div
            className={cn(
              'flex h-full min-h-0 flex-1 flex-col',
              scrollableBody ? 'overflow-y-auto' : 'overflow-hidden',
            )}
          >
            {children}
          </div>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>
  );
};

export default Modal;
