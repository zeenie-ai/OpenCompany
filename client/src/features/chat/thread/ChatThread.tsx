/**
 * The conversation's scroll area (design handoff chat, "Thread"): the
 * host's top slot (Home: a new hire's notes), the turns in a centred
 * column, and what the host shows after them.
 *
 * - **Sticks to the bottom.** While the newest turn is in view, anything
 *   that grows the thread (a new turn, text arriving, a markdown chunk
 *   loading) or takes room from it (the dock under it growing) keeps it in
 *   view. Scrolled up, it stays put and a "Jump to latest" button counts
 *   what arrived meanwhile; sending a message always scrolls down to it.
 *   Opening the thread starts at the newest turn.
 * - New turns rise in (`chat-rise`), never the ones already there on open.
 * - A divider names the day over the first message of each ("Today",
 *   "Yesterday", the weekday, the date), and another marks where the
 *   employee restarted.
 * - The turns are a log, so a screen reader hears new ones.
 */

import { ArrowDown } from 'lucide-react';
import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { animate } from '@/lib/motion';
import type { UiStateChange } from '../data/uiState';
import { prefersReducedMotion } from '@/lib/useReducedMotion';
import { cn } from '@/lib/utils';
import type { ChatUiActions } from '../genui/actions';
import type { ChatPersona } from '../host';
import { AssistantTurn } from '../turns/AssistantTurn';
import { UserTurn } from '../turns/UserTurn';
import type { ChatTurn } from './model';
import { dayKey, dayLabel } from './timeLabel';
import type { ArtifactRef } from '../data/parts';

/** Closer to the bottom than this counts as at the bottom. */
const STICK_PX = 48;
/** Scrolled further than this, the host draws its header border. */
const SCROLLED_PX = 6;

function RestartDivider({ name }: { name: string }) {
  return (
    <div className="flex items-center gap-3 text-xs text-fg-muted">
      <span aria-hidden className="h-px flex-1 bg-border-default" />
      <span className="text-center">{name} restarted — they start fresh from here</span>
      <span aria-hidden className="h-px flex-1 bg-border-default" />
    </div>
  );
}

function DayDivider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 font-mono text-2xs font-medium tracking-label text-fg-faint uppercase">
      <span aria-hidden className="h-px flex-1 bg-border-default" />
      <span>{label}</span>
      <span aria-hidden className="h-px flex-1 bg-border-default" />
    </div>
  );
}

export interface ChatThreadProps {
  persona: ChatPersona;
  turns: readonly ChatTurn[];
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  top?: ReactNode;
  afterThread?: ReactNode;
  emptyState?: ReactNode;
  liveNote?: string | null;
  compact: boolean;
  /** Esc stops a working run here, so its status line says so. */
  canStop?: boolean;
  /** What the buttons of an interface in a reply do. */
  uiActions?: ChatUiActions;
  onUiStateChange?: (partId: string, changes: UiStateChange[]) => void;
  /** Sends a suggested next question as the owner's message. */
  onFollowUp?: (text: string) => void;
  /** Shows a document a reply wrote, in the host's Canvas. */
  onOpenArtifact?: (artifact: ArtifactRef) => void;
  onScrolledChange?: (scrolled: boolean) => void;
}

export function ChatThread({
  persona,
  turns,
  loading,
  error,
  onRetry,
  top,
  afterThread,
  emptyState,
  liveNote,
  compact,
  canStop = false,
  uiActions,
  onUiStateChange,
  onFollowUp,
  onOpenArtifact,
  onScrolledChange,
}: ChatThreadProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const scrolled = useRef(false);
  const [unread, setUnread] = useState(0);
  const [away, setAway] = useState(false);

  const toBottom = useCallback((smooth: boolean) => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    atBottom.current = true;
    setAway(false);
    setUnread(0);
    if (smooth && !prefersReducedMotion()) scroller.scrollTo?.({ top: scroller.scrollHeight, behavior: 'smooth' });
    else scroller.scrollTop = scroller.scrollHeight;
  }, []);

  // Only scrolling up leaves the bottom: the positions a smooth scroll down
  // passes through on its way there do not.
  const lastTop = useRef(0);
  const onScroll = () => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    const top = scroller.scrollTop;
    if (scroller.scrollHeight - top - scroller.clientHeight <= STICK_PX) {
      atBottom.current = true;
      setAway(false);
      setUnread(0);
    } else if (top < lastTop.current) {
      atBottom.current = false;
      setAway(true);
    }
    lastTop.current = top;
    const isScrolled = top > SCROLLED_PX;
    if (isScrolled !== scrolled.current) {
      scrolled.current = isScrolled;
      onScrolledChange?.(isScrolled);
    }
  };

  // Whatever grows the thread, or takes room from it, keeps the newest turn
  // in view while it is.
  useLayoutEffect(() => {
    const content = contentRef.current;
    const scroller = scrollRef.current;
    if (!content || !scroller || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (atBottom.current) scroller.scrollTop = scroller.scrollHeight;
    });
    observer.observe(content);
    observer.observe(scroller);
    return () => observer.disconnect();
  }, []);

  // Turns that arrive: rise in; the owner's own message scrolls down to it;
  // anything else counts as unread while the thread is scrolled up.
  const shownKeys = useRef<Set<string> | null>(null);
  useLayoutEffect(() => {
    if (loading) return;
    const keys = new Set(turns.map((turn) => turn.key));
    const previous = shownKeys.current;
    shownKeys.current = keys;
    if (previous === null) {
      toBottom(false);
      return;
    }
    const added = turns.filter((turn) => !previous.has(turn.key) && turn.kind !== 'divider');
    if (added.length === 0) return;
    const elements = new Map(
      Array.from(contentRef.current?.querySelectorAll<HTMLElement>('[data-turn-key]') ?? []).map((element) => [element.dataset.turnKey, element]),
    );
    added.forEach((turn, index) => {
      animate(elements.get(turn.key), [{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }], {
        duration: 'chat-rise',
        easing: 'spring',
        delay: 60 + index * 70,
      });
    });
    if (added.some((turn) => turn.kind === 'user')) toBottom(true);
    else if (!atBottom.current) setUnread((count) => count + added.length);
  }, [turns, loading, toBottom]);

  const now = new Date();
  const lastTurn = turns.at(-1)?.key;
  // The turns that start a day, with the day's name. A reply still on its
  // way has no day yet.
  const dayStarts = new Map<string, string>();
  let shownDay: string | null = null;
  for (const turn of turns) {
    const timestamp = turn.kind === 'divider' ? null : (turn.message?.timestamp ?? null);
    const day = dayKey(timestamp);
    if (day && day !== shownDay) {
      dayStarts.set(turn.key, dayLabel(timestamp, now));
      shownDay = day;
    }
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} onScroll={onScroll} data-scrollable className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
        <div
          ref={contentRef}
          className={cn(
            'mx-auto flex w-full flex-col',
            compact ? 'gap-4 px-3 py-3' : 'max-w-(--w-chat-column) gap-7 px-6 pt-7 pb-8',
          )}
        >
          {top}
          {loading && (
            <div aria-busy className="flex flex-col gap-4">
              <Skeleton className="h-10 w-3/5 self-end rounded-draft" />
              <Skeleton className="h-16 w-4/5 rounded-card" />
            </div>
          )}
          {error && (
            <div className="flex flex-col items-center gap-2 py-6 text-center">
              <p className="m-0 text-sm text-fg-default">Couldn’t load the conversation.</p>
              <Button variant="quiet" onClick={onRetry} className="border-border-default text-fg-default">
                Try again
              </Button>
            </div>
          )}
          {!loading && !error && turns.length === 0 && emptyState}
          <div
            role="log"
            aria-live="polite"
            aria-label={`Conversation with ${persona.name}`}
            className={cn('flex flex-col', compact ? 'gap-4' : 'gap-7', turns.length === 0 && 'hidden')}
          >
            {turns.flatMap((turn) => {
              if (turn.kind === 'divider') return [<RestartDivider key={turn.key} name={persona.name} />];
              const latest = turn.key === lastTurn;
              const dayName = dayStarts.get(turn.key);
              const row = (
                <div key={turn.key} data-turn-key={turn.key}>
                  {turn.kind === 'user' ? (
                    <UserTurn message={turn.message} now={now} latest={latest} compact={compact} />
                  ) : (
                    <AssistantTurn
                      message={turn.message}
                      run={turn.run}
                      work={turn.work}
                      liveUi={turn.liveUi}
                      sources={turn.sources}
                      persona={persona}
                      now={now}
                      latest={latest}
                      compact={compact}
                      liveNote={liveNote}
                      canStop={canStop}
                      uiActions={uiActions}
                      onUiStateChange={onUiStateChange}
                      onFollowUp={onFollowUp}
                      onOpenArtifact={onOpenArtifact}
                    />
                  )}
                </div>
              );
              return dayName ? [<DayDivider key={`day:${turn.key}`} label={dayName} />, row] : [row];
            })}
          </div>
          {afterThread}
        </div>
      </div>
      {!compact && <span aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-7 bg-linear-to-b from-transparent to-bg-app" />}
      <Button
        variant="quiet"
        onClick={() => toBottom(true)}
        aria-label={unread > 0 ? `Jump to latest, ${unread} new` : 'Jump to latest'}
        tabIndex={away ? 0 : -1}
        aria-hidden={!away}
        className={cn(
          'absolute bottom-3 left-1/2 z-10 h-8.5 min-w-8.5 -translate-x-1/2 gap-1.5 rounded-pill border-border-default bg-bg-elevated px-2.5 text-xs text-fg-default shadow-card-hover transition-[opacity,translate] duration-(--dur-slow)',
          away ? 'translate-y-0 opacity-100' : 'pointer-events-none translate-y-2 opacity-0',
        )}
      >
        <ArrowDown aria-hidden className="size-3.75" strokeWidth={2.2} />
        {unread > 0 && <span>{unread} new</span>}
      </Button>
    </div>
  );
}
