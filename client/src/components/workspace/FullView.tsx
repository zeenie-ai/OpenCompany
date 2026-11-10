import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface FullViewState {
  full: boolean;
  toggle: () => void;
  button: RefObject<HTMLButtonElement | null>;
}

const FullViewContext = createContext<FullViewState | null>(null);

/** A live view's box (the Browser's, the phone's). Dev mode's Full view
 *  (`FullViewButton`, placed by the view among its own controls) fills the
 *  screen with this box as it is; it never remounts the view's canvas or
 *  connection. Home's dock has Expand instead and shows no such button. */
export function FullView({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const [full, setFull] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const element = root.current;
    let wasFull = false;
    const changed = () => {
      const next = document.fullscreenElement === element;
      setFull(next);
      if (wasFull && !next) button.current?.focus();
      wasFull = next;
    };
    document.addEventListener('fullscreenchange', changed);
    return () => {
      document.removeEventListener('fullscreenchange', changed);
      if (document.fullscreenElement === element) void document.exitFullscreen().catch(() => {});
    };
  }, []);
  const state = useMemo<FullViewState>(() => ({
    full,
    button,
    toggle: () => {
      setError('');
      void (async () => {
        try {
          if (document.fullscreenElement === root.current) await document.exitFullscreen();
          else if (root.current?.requestFullscreen) await root.current.requestFullscreen();
          else setError('Full view is unavailable in this browser. Use the Workspace expand button instead.');
        } catch {
          setError('Could not open full view. Try again or use the Workspace expand button.');
        }
      })();
    },
  }), [full]);
  return (
    <FullViewContext.Provider value={state}>
      <div ref={root} data-full-view={full} className={`group/fullview flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-bg-app ${full ? 'h-screen w-screen' : ''}`}>
        {error && <p role="alert" className="m-0 shrink-0 px-2 py-1 text-xs text-destructive">{error}</p>}
        {children}
      </div>
    </FullViewContext.Provider>
  );
}

/** Dev mode's Full view, for a live view's row of controls: a round quiet
 *  button like the ones beside it. `label` names the view ("Browser"). */
export function FullViewButton({ label, className }: { label: string; className?: string }) {
  const view = useContext(FullViewContext);
  if (!view) return null;
  const name = view.full ? `Exit ${label.toLowerCase()} full view` : `${label} full view`;
  return (
    <Button
      ref={view.button}
      size="icon-sm"
      variant="quiet"
      aria-label={name}
      title={view.full ? 'Exit full view' : 'Full view'}
      aria-pressed={view.full}
      onClick={view.toggle}
      className={cn('size-7.5 rounded-full', className)}
    >
      {view.full ? <Minimize2 aria-hidden className="size-3.5" strokeWidth={1.75} /> : <Maximize2 aria-hidden className="size-3.5" strokeWidth={1.75} />}
    </Button>
  );
}
