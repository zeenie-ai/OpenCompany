import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Maximize2, Minimize2, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';

/** Fullscreen the existing viewer; never remount its canvas or connection.
 *  `chrome="home"` (Home's dock, which has its own Expand) draws only the
 *  viewer: no bar, no controls and no Full view; `toolbar` and `controls`
 *  are the editor's. */
export function FullView({ label, toolbar, controls, chrome = 'dev', children }: { label: string; toolbar?: ReactNode; controls?: ReactNode; chrome?: 'dev' | 'home'; children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const [full, setFull] = useState(false);
  const [error, setError] = useState('');
  const [controlsOpen, setControlsOpen] = useState(false);
  const controlsId = useId();
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
  const toggle = async () => {
    setError('');
    try {
      if (document.fullscreenElement === root.current) await document.exitFullscreen();
      else if (root.current?.requestFullscreen) await root.current.requestFullscreen();
      else setError('Full view is unavailable in this browser. Use the Workspace expand button instead.');
    } catch {
      setError('Could not open full view. Try again or use the Workspace expand button.');
    }
  };
  return <div ref={root} data-full-view={full} className={`group/fullview flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-bg-app ${full ? 'h-screen w-screen' : ''}`}>
    {chrome === 'dev' && <>
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5 border-b border-border-default px-2 py-1.5">
        {toolbar ?? (full && <span className="mr-auto text-sm font-medium">{label} <span className="text-xs font-normal text-fg-muted">· Esc to return</span></span>)}
        {controls && <Button size="icon-sm" variant="ghost" aria-label={`${label} controls`} title={controlsOpen ? 'Hide controls' : 'Show controls'} aria-expanded={controlsOpen} aria-controls={controlsId} onClick={() => setControlsOpen((open) => !open)}>
          <SlidersHorizontal className="size-4" />
        </Button>}
        <Button ref={button} size="sm" variant="outline" aria-label={full ? `Exit ${label.toLowerCase()} full view` : `${label} full view`} aria-pressed={full} onClick={() => void toggle()}>
          {full ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}{full ? 'Exit full view' : 'Full view'}
        </Button>
      </div>
      {error && <p role="alert" className="m-0 shrink-0 px-2 py-1 text-xs text-destructive">{error}</p>}
      {controls && <div id={controlsId} hidden={!controlsOpen} className="max-h-[40%] shrink-0 space-y-2 overflow-y-auto border-b border-border-default p-2">{controls}</div>}
    </>}
    {children}
  </div>;
}
