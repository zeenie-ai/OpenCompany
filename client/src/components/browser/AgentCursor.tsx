/**
 * The employee's cursor on the live browser picture (design handoff
 * "Workspace panel"): an arrow with their name that glides to each action
 * the server reports (`agent_action`, nodes/browser), a ripple where they
 * clicked, and a ring round the field they typed into or chose from. The
 * page's own caret and text show in the picture itself.
 *
 * It lies over the picture the way the canvas draws it (contained and
 * centred), in a box the picture's shape (container units), so positions
 * are fractions of the picture (`framePosition`) and a resize or Full view
 * needs no measuring. The host hides it while the owner or another viewer
 * has control; it stays mounted meanwhile, so nothing replays when it shows
 * again. Decorative: the status line says what the employee is doing.
 */

import { MousePointer2 } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';
import { animate, dur, finished } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { framePosition, type BrowserAgentAction, type BrowserFrameHeader } from './protocol';

/** The picture an action is drawn on: its size in pixels and its header. */
export interface PictureShape {
  width: number;
  height: number;
  header: BrowserFrameHeader;
}

const percent = (fraction: number) => `${fraction * 100}%`;

export function AgentCursor({
  action,
  picture,
  name,
  hidden,
}: {
  action: BrowserAgentAction;
  picture: PictureShape;
  /** On the tag beside the arrow; no tag without one. */
  name?: string;
  hidden: boolean;
}) {
  const cursorRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLSpanElement>(null);
  const rippleRef = useRef<HTMLSpanElement>(null);
  const last = useRef<{ id: number; left: string; top: string } | null>(null);
  const at = framePosition(action.point.x, action.point.y, picture.header);
  const left = percent(at.x);
  const top = percent(at.y);
  const onPicture = at.x >= 0 && at.x <= 1 && at.y >= 0 && at.y <= 1;
  const ring = action.box && {
    from: framePosition(action.box.left, action.box.top, picture.header),
    to: framePosition(action.box.left + action.box.width, action.box.top + action.box.height, picture.header),
  };

  useLayoutEffect(() => {
    const previous = last.current;
    last.current = { id: action.id, left, top };
    // The same action on a picture of another shape: nothing new happened.
    if (previous?.id === action.id) return;
    const glide = previous
      ? animate(cursorRef.current, [{ left: previous.left, top: previous.top }, { left, top }], { duration: 'cursor-glide', easing: 'reveal' })
      : animate(cursorRef.current, [{ opacity: 0 }, { opacity: 1 }], { duration: 'default' });
    animate(ringRef.current, [{ opacity: 0 }, { opacity: 1 }], { duration: 'default', delay: previous ? dur('cursor-glide') : 0 });
    if (action.action === 'click') {
      void finished(glide).then(() =>
        animate(rippleRef.current, [{ opacity: 0.9, transform: 'scale(0.4)' }, { opacity: 0, transform: 'scale(1.6)' }], { duration: 'cursor-ripple' }),
      );
    }
  }, [action.id, action.action, left, top]);

  return (
    <div
      aria-hidden
      className={cn(
        'pointer-events-none absolute inset-0 flex items-center justify-center [container-type:size]',
        (hidden || !onPicture) && 'invisible',
      )}
    >
      <div
        className="relative"
        style={{ aspectRatio: `${picture.width} / ${picture.height}`, width: `min(100cqw, 100cqh * ${picture.width / picture.height})` }}
      >
        {ring && (
          <span
            key={action.id}
            ref={ringRef}
            className="absolute rounded-sm border-2 border-(--agent-cursor-tag)"
            style={{ left: percent(ring.from.x), top: percent(ring.from.y), width: percent(ring.to.x - ring.from.x), height: percent(ring.to.y - ring.from.y) }}
          />
        )}
        <span ref={rippleRef} className="absolute size-7 -translate-1/2 rounded-full border-2 border-(--agent-cursor-tag) opacity-0" style={{ left, top }} />
        <div ref={cursorRef} className="absolute" style={{ left, top }}>
          {/* Moved back by the arrow's tip, so the tip is on the point. */}
          <MousePointer2 aria-hidden strokeWidth={1.5} className="size-5.5 -translate-1/6 fill-(--agent-cursor-fill) text-(--agent-cursor-edge)" />
          {name && (
            <span className="absolute top-4.5 left-3.5 flex h-5 items-center rounded-pill bg-(--agent-cursor-tag) px-1.75 text-2xs font-semibold whitespace-nowrap text-(--agent-cursor-tag-ink)">
              {name}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
