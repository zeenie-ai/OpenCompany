/**
 * The employee's side of a turn (design handoff chat, "Assistant row"):
 * their avatar on the left and, with no bubble, in order: what they did on
 * the way (the steps disclosure), what they said in markdown, any interface
 * they showed (GeneratedUiBlock), and anything they want to send, waiting for
 * the owner (ApprovalCard).
 *
 * While their run works the avatar spins its ring; skeleton lines stand in
 * for text that has not come yet; the answer streams in with a caret after
 * it; and a status line says "Thinking" or "Writing · N tok/s" and that Esc
 * stops it. Text written beside tool calls shows muted until the answer
 * replaces it. A run that waits for Resume, failed or was stopped says so
 * where its answer would be.
 *
 * `chat-msg chat-msg-bot` (on the text only) is the theme hook the stylized
 * themes paint as a bubble.
 */

import { CircleAlert, Pause, RotateCcw, Square } from 'lucide-react';
import { Suspense, lazy, useMemo, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { isLiveRun, type RunSnapshot } from '@/lib/agui/reduceRun';
import type { UiStateChange } from '@/lib/jsonRender/uiState';
import { cn } from '@/lib/utils';
import { ApprovalCard } from '../approval/ApprovalCard';
import {
  liveApprovalIds,
  liveArtifacts,
  mergeArtifacts,
  savedApprovalIds,
  savedArtifacts,
  savedFollowups,
  savedSources,
  savedUiParts,
  type ArtifactRef,
  type SourceItem,
  type UiPart,
} from '../data/parts';
import type { ChatMessage } from '../data/schemas';
import type { ChatUiActions } from '../genui/actions';
import type { ChatPersona } from '../host';
import { CitationContext, NO_CITATIONS, type CitationInfo } from '../markdown/citationContext';
import { citationOrder } from '../markdown/citations';
import { ChatAvatar } from '../thread/ChatAvatar';
import type { TurnWork } from '../thread/model';
import { timeLabel } from '../thread/timeLabel';
import { useComposerStore } from '../state/composerStore';
import { useTurnActions } from '../thread/turnActions';
import { ArtifactCard } from './ArtifactCard';
import { GeneratedUiBlock } from './GeneratedUiBlock';
import { FollowUps } from './FollowUps';
import { ReplyActions } from './ReplyActions';
import { failureLines, liveLabel, liveText } from './runCopy';
import { SourceChips } from './SourceChips';
import { StatusLine } from './StatusLine';
import { StepsDisclosure } from './StepsDisclosure';
import { TurnMeta } from './TurnMeta';
import { useWritingRate } from './useWritingRate';
import { useChatWorkflowControl } from '../data/control';

const NO_UI: UiPart[] = [];

/** Whether the owner has written something in this chat's box. */
function useDrafting(): boolean {
  const sessionId = useTurnActions()?.sessionId ?? null;
  return useComposerStore((state) => Boolean(sessionId && state.drafts[sessionId]?.text.trim()));
}

// Its own chunk: the markdown stack stays out of the first load.
const ReplyMarkdown = lazy(() => import('../markdown/ReplyMarkdown'));

function Thinking() {
  return (
    <div aria-hidden data-thinking className="flex flex-col gap-2.25 pt-0.5">
      <span className="opencompany-shimmer h-3 w-[78%] rounded-md" />
      <span className="opencompany-shimmer h-3 w-[92%] rounded-md [animation-delay:120ms]" />
      <span className="opencompany-shimmer h-3 w-[54%] rounded-md [animation-delay:240ms]" />
    </div>
  );
}

function Note({ icon, tone = 'muted', children }: { icon: 'alert' | 'pause' | 'stop'; tone?: 'muted' | 'alert'; children: ReactNode }) {
  const Icon = icon === 'alert' ? CircleAlert : icon === 'pause' ? Pause : Square;
  return (
    <div className="flex items-start gap-2 text-sm">
      <Icon aria-hidden className={cn('mt-0.75 size-3.5 shrink-0', tone === 'alert' ? 'text-action-stop-ink' : 'text-fg-faint')} />
      <div className="flex min-w-0 flex-col gap-0.5 text-fg-muted">{children}</div>
    </div>
  );
}

export function AssistantTurn({
  message,
  run,
  work,
  liveUi = NO_UI,
  sources,
  persona,
  now,
  latest,
  compact,
  liveNote,
  canStop = false,
  uiActions,
  onUiStateChange,
  onFollowUp,
  onOpenArtifact,
}: {
  message: ChatMessage | null;
  run: RunSnapshot | null;
  work: TurnWork | null;
  /** Interfaces the run streamed, until the saved reply carries them. */
  liveUi?: UiPart[];
  /** The conversation's sources as of this answer, by number (the thread's
   *  `ChatTurn.sources`); without them, the answer's own. */
  sources?: ReadonlyMap<number, SourceItem>;
  persona: ChatPersona;
  now: Date;
  latest: boolean;
  compact: boolean;
  /** Said on the status line while the run works (an automatic retry). */
  liveNote?: string | null;
  /** The pane stops a working run on Esc, so the status line says so. */
  canStop?: boolean;
  /** What the buttons of an interface in the reply do. */
  uiActions?: ChatUiActions;
  onUiStateChange?: (partId: string, changes: UiStateChange[]) => void;
  /** Sends a suggested next question; absent where they cannot go. */
  onFollowUp?: (text: string) => void;
  /** Shows a document the reply wrote, in the host's Canvas. */
  onOpenArtifact?: (artifact: ArtifactRef) => void;
}) {
  const queued = run?.state === 'queued';
  const control = useChatWorkflowControl(run?.workflowId ?? null, run);
  const controlled = control?.execution_control_version === 1 && Boolean(run && isLiveRun(run));
  const suspended = controlled && control.state === 'paused';
  const live = Boolean(run && isLiveRun(run) && !queued && !suspended);
  // A saved answer is the answer; until then, what the run streamed.
  const streamed = liveText(message ? null : run);
  const text = message ? message.text : streamed.text;
  const streaming = live && streamed.streaming;
  const narration = streamed.narration;
  const rate = useWritingRate(text, streaming, now);
  const failure = run?.state === 'error' ? failureLines(run.error, persona.name) : null;
  // A run that ended without saving an answer can be tried again from its
  // note (an answer it saved has Try again in its bar).
  const actions = useTurnActions();
  const question = run?.userMessageId ?? null;
  const retry =
    !message && latest && actions && question && (run?.state === 'stopped' || run?.state === 'error') ? (
      <div>
        <Button
          variant="quiet"
          size="sm"
          disabled={actions.busy}
          onClick={() => actions.regenerate(question)}
          className="-ml-2 h-7 gap-1.5 px-2 text-xs font-semibold text-fg-default"
        >
          <RotateCcw aria-hidden className="size-3.25" strokeWidth={2} />
          Try again
        </Button>
      </div>
    ) : null;
  const saved = message ? savedUiParts(message.parts) : NO_UI;
  const interfaces = saved.length > 0 ? saved : liveUi;
  // The documents it wrote, at the latest version the reply or the run names.
  const artifacts = useMemo(
    () => mergeArtifacts(savedArtifacts(message?.parts), liveArtifacts(run?.activities)),
    [message?.parts, run?.activities],
  );
  // The drafts it made: named on the saved reply, or arriving with the run.
  const approvals = useMemo(
    () => [...new Set([...savedApprovalIds(message?.parts), ...liveApprovalIds(run?.activities)])],
    [message?.parts, run?.activities],
  );
  // Only under the latest answer, once it is done, not stopped, and while
  // the owner is not writing something else.
  const drafting = useDrafting();
  const followups =
    message && latest && !live && onFollowUp && !drafting && message.status !== 'stopped' && run?.state !== 'stopped'
      ? savedFollowups(message.parts)
      : [];
  const citations = useMemo<CitationInfo>(() => {
    if (!message) return NO_CITATIONS;
    const known = sources ?? new Map(savedSources(message.parts).map((source) => [source.n, source] as const));
    if (known.size === 0) return NO_CITATIONS;
    return { sources: known, order: citationOrder(text, new Set(known.keys())) };
  }, [message, text, sources]);

  return (
    <div
      data-turn="assistant"
      data-message={message?.id}
      data-run={run?.runId}
      aria-busy={live || undefined}
      className={cn('chat-turn-bot group/turn flex items-start', compact ? 'gap-2.5' : 'gap-3.5')}
    >
      <ChatAvatar persona={persona} live={live} compact={compact} />
      <div className="flex min-w-0 flex-1 flex-col gap-2.5">
        {work && <StepsDisclosure work={suspended ? { ...work, live: false } : work} compact={compact} />}
        {live && !text && <Thinking />}
        {text && (
          <div
            data-narration={narration || undefined}
            className={cn(
              'chat-msg chat-msg-bot wrap-anywhere',
              narration ? 'text-fg-muted' : 'text-fg-default',
              compact ? 'leading-normal' : 'text-md leading-relaxed',
            )}
          >
            <Suspense fallback={<p className="m-0 whitespace-pre-wrap">{text}</p>}>
              <CitationContext.Provider value={citations}>
                <ReplyMarkdown text={text} streaming={streaming} />
              </CitationContext.Provider>
            </Suspense>
          </div>
        )}
        {uiActions &&
          interfaces.map((part) => (
            <GeneratedUiBlock
              key={part.partId}
              part={part}
              live={!message && live}
              actions={uiActions}
              onStateChange={onUiStateChange}
            />
          ))}
        {artifacts.map((artifact) => (
          <ArtifactCard key={artifact.itemId} artifact={artifact} onOpen={onOpenArtifact} />
        ))}
        {approvals.map((id) => (
          <ApprovalCard key={id} approvalId={id} />
        ))}
        {live && run && (
          <StatusLine
            label={controlled && control.state === 'pausing' ? 'Stopping…'
              : controlled && control.state === 'resuming' ? 'Resuming…' : liveLabel(run, streaming)}
            rate={rate}
            note={liveNote}
            stopHint={canStop && run.state !== 'stopping' && !(controlled && control.state !== 'running')}
            compact={compact}
          />
        )}
        {(queued || suspended) && (
          <Note icon="pause">
            <p className="m-0">Waiting for you to resume {persona.name}.</p>
          </Note>
        )}
        {run?.state === 'stopped' && (
          <Note icon="stop">
            <p className="m-0">You stopped this reply.</p>
            {retry}
          </Note>
        )}
        {failure && (
          <Note icon="alert" tone="alert">
            <p className="m-0 text-fg-default">{failure.headline}</p>
            {failure.detail && <p className="m-0">{failure.detail}</p>}
            {run?.error?.hint && <p className="m-0">{run.error.hint}</p>}
            {retry}
          </Note>
        )}
        {citations.order.size > 0 && <SourceChips sources={citations.sources} order={citations.order} />}
        {followups.length > 0 && onFollowUp && <FollowUps items={followups} onPick={onFollowUp} />}
        {message &&
          (live ? (
            <TurnMeta shown={latest}>{timeLabel(message.timestamp, now)}</TurnMeta>
          ) : (
            <ReplyActions message={message} latest={latest} now={now} />
          ))}
      </div>
    </div>
  );
}
