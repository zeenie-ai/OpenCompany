/**
 * A draft waiting for the owner's OK, in the chat (design handoff chat,
 * "Approval card"): who it goes to, through which app, the message as it
 * will go, and Discard / Edit / Send. After Send it says when it goes, with
 * Undo while it can; then that it went (or did not, with Try again). A
 * discarded draft can be restored while its window lasts. Ctrl/Cmd+Enter in
 * the edit box sends it.
 *
 * The draft itself lives on the server (data/approvals.ts); the card holds
 * only the edit in progress. `chat-approval` is the theme hook.
 */

import { Check, Link2, Loader2, MessageCircle, Pause, Pencil, RotateCcw, Send, ShieldCheck, Undo2 } from 'lucide-react';
import { useContext, useEffect, useId, useLayoutEffect, useState, type KeyboardEvent } from 'react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import type { ChatApproval } from '../data/approvals';
import { ApprovalsContext } from './context';
import { approvalView, needsTicks, type CardTone, type CardView } from './view';

const PILL_TONE: Record<CardTone, string> = {
  waiting: 'border-action-config-border bg-action-config-soft text-action-config-ink',
  sending: 'border-action-run-border bg-action-run-soft text-action-run-ink',
  sent: 'border-action-run-border bg-action-run-soft text-action-run-ink',
  muted: 'border-border-default bg-bg-hover text-fg-muted',
  failed: 'border-action-stop-border bg-action-stop-soft text-action-stop-ink',
};

const DOT_TONE: Record<CardTone, string> = {
  waiting: 'bg-action-config-ink',
  sending: 'bg-action-run-ink',
  sent: 'bg-action-run-ink',
  muted: 'bg-fg-faint',
  failed: 'bg-action-stop-ink',
};

const FRAME: Record<CardTone, string> = {
  waiting: 'border-action-config-border ring-[3px] ring-action-config-soft',
  sending: 'border-action-run-border',
  sent: 'border-action-run-border',
  muted: 'border-border-default',
  failed: 'border-action-stop-border',
};

const HEAD: Record<CardTone, string> = {
  waiting: 'bg-action-config-soft',
  sending: 'bg-action-run-soft',
  sent: 'bg-action-run-soft',
  muted: '',
  failed: 'bg-action-stop-soft',
};

function isMac(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
}

/** The time the card reads: taken again whenever the draft moves, and
 *  every second while a countdown is on screen. */
function useCardNow(revision: number, ticking: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useLayoutEffect(() => {
    setNow(Date.now());
  }, [revision, ticking]);
  useEffect(() => {
    if (!ticking) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [ticking]);
  return now;
}

export function ApprovalCard({ approvalId, className }: { approvalId: string; className?: string }) {
  const shared = useContext(ApprovalsContext);
  const approval = shared?.approvals.byId.get(approvalId);
  if (!shared || !approval) return null;
  return <Card approval={approval} className={className} />;
}

function Card({ approval, className }: { approval: ChatApproval; className?: string }) {
  const shared = useContext(ApprovalsContext)!;
  const { approvals, name, askFirst, compact, decide, deciding } = shared;
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(approval.body);
  const [subject, setSubject] = useState(approval.subject ?? '');
  const busy = deciding.has(approval.id);
  const boxId = useId();

  // A draft that left "pending" ends any edit; a new body (the server's)
  // shows when not editing.
  useEffect(() => {
    if (approval.status !== 'pending') setEditing(false);
  }, [approval.status]);
  useEffect(() => {
    if (!editing) {
      setText(approval.body);
      setSubject(approval.subject ?? '');
    }
  }, [approval.body, approval.subject, editing]);

  const [ticking, setTicking] = useState(false);
  const now = useCardNow(approval.revision, ticking);
  const view: CardView = approvalView(approval, { name, nowMs: now, offsetMs: approvals.offsetMs, editing });
  const countdown = needsTicks(view);
  useEffect(() => setTicking(countdown), [countdown]);

  const edited = text.trim() !== approval.body.trim() || (subject.trim() || null) !== (approval.subject ?? null);
  const tooLong = text.length > approval.maxLength;
  const canSend = approval.status === 'pending' && !busy && text.trim().length > 0 && !tooLong;

  const send = () => {
    if (!canSend) return;
    decide({
      approval,
      decision: 'send',
      ...(edited ? { text: text.trim() } : {}),
      ...(edited && approval.subject !== null ? { subject: subject.trim() } : {}),
    });
  };

  const onBoxKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      event.stopPropagation();
      send();
    }
  };

  const to = approval.recipientLabel || approval.recipient || 'them';
  const linked = approval.uiPartId !== null && approval.status === 'pending' && !edited;

  return (
    <div
      data-approval={approval.id}
      data-status={approval.status}
      className={cn(
        'chat-approval flex w-full max-w-136 flex-col overflow-hidden rounded-card border bg-bg-panel shadow-card transition-[border-color,box-shadow] duration-(--dur-slow)',
        FRAME[view.tone],
        className,
      )}
    >
      <div className={cn('flex items-center gap-2.5 border-b border-border-default px-3.5 py-2.75 transition-colors duration-(--dur-slow)', HEAD[view.tone])}>
        <span
          aria-hidden
          className="grid size-7.5 flex-none place-items-center rounded-lg border border-node-tool-border bg-action-run-soft text-action-run-ink"
        >
          <MessageCircle className="size-3.75" strokeWidth={1.9} />
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="text-sm font-semibold text-fg-default">{view.title}</span>
          <span className="truncate text-xs text-fg-muted">{view.sub}</span>
        </div>
        <span className={cn('flex h-6 flex-none items-center gap-1.5 rounded-pill border px-2.25 text-xs font-medium whitespace-nowrap', PILL_TONE[view.pill.tone])}>
          <span aria-hidden className={cn('size-1.5 rounded-full', DOT_TONE[view.pill.tone], view.pill.pulse && 'opencompany-pip-pulse')} />
          {view.pill.label}
        </span>
      </div>

      <div className={cn('flex flex-col gap-2.5', compact ? 'p-3' : 'p-3.5')}>
        <div className="flex items-center gap-2 text-xs text-fg-muted">
          <span aria-hidden className="grid size-5.5 flex-none place-items-center rounded-full bg-action-stop-soft text-2xs font-semibold text-action-stop-ink">
            {(to.charAt(0) || '?').toUpperCase()}
          </span>
          <span className="min-w-0 truncate">
            To <span className="font-medium text-fg-default">{to}</span>
            <span className="text-fg-faint"> · {approval.channel}</span>
          </span>
          {linked && (
            <span className="ml-auto flex items-center gap-1.25 font-mono text-2xs font-medium text-action-tools-ink">
              <Link2 aria-hidden className="size-2.75" strokeWidth={2.2} />
              Linked to the form above
            </span>
          )}
        </div>

        {editing ? (
          <div className="flex flex-col gap-1.5">
            {approval.subject !== null && (
              <input
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                aria-label="Subject"
                className="h-8 rounded-lg border border-action-tools-border bg-bg-input px-3 text-sm text-fg-default outline-none"
              />
            )}
            <Textarea
              id={boxId}
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={onBoxKeyDown}
              rows={3}
              aria-label="Edit the draft"
              autoFocus
              className="min-h-21 resize-y border-action-tools-border bg-bg-input text-base leading-normal ring-[3px] ring-node-agent-soft"
            />
            <span className={cn('self-end font-mono text-2xs font-medium', tooLong ? 'text-action-stop-ink' : 'text-fg-faint')}>
              {text.length} / {approval.maxLength}
            </span>
          </div>
        ) : (
          <div className="flex justify-end">
            <div
              className={cn(
                'flex max-w-[92%] flex-col gap-1 rounded-[14px_4px_14px_14px] border px-3 pt-2.25 pb-1.5 transition-[background-color,border-color,opacity] duration-(--dur-slow)',
                view.bubble === 'sent' ? 'border-action-run-border bg-action-run-soft' : 'border-border-default bg-bg-elevated',
                view.bubble === 'struck' && 'opacity-55',
              )}
            >
              {approval.subject && <span className="text-xs font-semibold text-fg-default">{approval.subject}</span>}
              <span className={cn('text-base leading-normal whitespace-pre-wrap text-fg-default', view.bubble === 'struck' && 'line-through')}>
                {approval.body || '(no message)'}
              </span>
              <span className="flex items-center gap-1 self-end text-2xs text-fg-faint">
                {view.when}
                {view.bubble === 'sent' && <Check aria-hidden className="size-3 text-action-save-ink" strokeWidth={2.4} />}
              </span>
            </div>
          </div>
        )}

        {approval.details.length > 0 && (
          <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            {approval.details.map((row) => (
              <div key={row.label} className="contents">
                <dt className="text-fg-faint">{row.label}</dt>
                <dd className="m-0 min-w-0 truncate text-fg-default">{row.value}</dd>
              </div>
            ))}
          </dl>
        )}

        {view.reassure && (
          <span className="flex items-center gap-1.5 text-xs text-fg-faint">
            <ShieldCheck aria-hidden className="size-3" strokeWidth={2} />
            {askFirst ? 'Ask first is on, so nothing goes out until you send it.' : 'Nothing goes out until you send it.'}
          </span>
        )}
      </div>

      <Footer view={view} approval={approval} busy={busy} editing={editing} canSend={canSend} onSend={send} onToggleEdit={() => setEditing((on) => !on)} />
    </div>
  );
}

function Footer({
  view,
  approval,
  busy,
  editing,
  canSend,
  onSend,
  onToggleEdit,
}: {
  view: CardView;
  approval: ChatApproval;
  busy: boolean;
  editing: boolean;
  canSend: boolean;
  onSend: () => void;
  onToggleEdit: () => void;
}) {
  const { decide } = useContext(ApprovalsContext)!;
  const footer = view.footer;
  const bar = 'flex flex-wrap items-center gap-2 border-t border-border-default px-3.5 py-2.5';

  if (footer.kind === 'decide') {
    return (
      <div className={cn(bar, 'bg-bg-app')}>
        <Button
          variant="quiet"
          size="sm"
          disabled={busy}
          onClick={() => decide({ approval, decision: 'discard' })}
          className="font-semibold hover:bg-action-stop-soft hover:text-action-stop-ink"
        >
          Discard
        </Button>
        {approval.editable && (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            aria-pressed={editing}
            onClick={onToggleEdit}
            className={cn('gap-1.5 font-semibold', editing && 'border-action-tools-border bg-action-tools-soft text-action-tools-ink')}
          >
            <Pencil aria-hidden className="size-3.25" />
            {editing ? 'Done' : 'Edit'}
          </Button>
        )}
        <span aria-hidden className="ml-auto flex items-center font-mono text-2xs font-medium text-fg-faint">
          <kbd className="rounded-sm border border-border-default bg-bg-panel px-1.25 py-px font-[inherit]">{isMac() ? '⌘ ↵' : 'Ctrl ↵'}</kbd>
        </span>
        <ActionButton intent="run" disabled={!canSend} onClick={onSend} className="gap-1.75">
          <Send aria-hidden className="size-3.25" />
          Send
        </ActionButton>
      </div>
    );
  }
  if (footer.kind === 'undo') {
    return (
      <div className={cn(bar, 'bg-bg-app text-xs text-fg-muted')} aria-live="off">
        <Loader2 aria-hidden className="opencompany-spinner size-3.5 text-action-run-ink" />
        <span className="flex-1">Sends in {footer.seconds}s</span>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => decide({ approval, decision: 'undo' })} className="gap-1.5 font-semibold tabular-nums">
          <Undo2 aria-hidden className="size-3.25" />
          Undo · {footer.seconds}s
        </Button>
      </div>
    );
  }
  if (footer.kind === 'sending') {
    return (
      <div className={cn(bar, 'bg-bg-app text-xs text-fg-muted')} role="status">
        <Loader2 aria-hidden className="opencompany-spinner size-3.5 text-action-run-ink" />
        Sending on {approval.channel}…
      </div>
    );
  }
  if (footer.kind === 'waiting') {
    return (
      <div className={cn(bar, 'bg-bg-app text-xs text-fg-muted')} role="status">
        <Pause aria-hidden className="size-3.5" />
        {footer.text}
      </div>
    );
  }
  if (footer.kind === 'sent') {
    return (
      <div className={cn(bar, 'bg-action-run-soft')} role="status">
        <span aria-hidden className="grid size-5 flex-none place-items-center rounded-full border border-action-run-border bg-action-run-soft">
          <Check className="opencompany-draw size-2.75 text-action-run-ink" strokeWidth={3} />
        </span>
        <span className="min-w-0 flex-1 text-xs text-fg-default">{footer.text}</span>
      </div>
    );
  }
  if (footer.kind === 'discarded') {
    return (
      <div className={cn(bar, 'bg-bg-app')}>
        <span className="min-w-0 flex-1 text-xs text-fg-muted" role="status">
          {footer.text}
        </span>
        {footer.canRestore && (
          // Its countdown ticks every second: never announced.
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => decide({ approval, decision: 'restore' })}
            className="gap-1.5 font-semibold tabular-nums"
            aria-live="off"
          >
            <RotateCcw aria-hidden className="size-3.25" />
            {footer.restoreSeconds !== null ? `Restore · ${footer.restoreSeconds}s` : 'Restore'}
          </Button>
        )}
      </div>
    );
  }
  if (footer.kind === 'failed') {
    return <FailedFooter className={bar} text={footer.text} unknown={footer.unknown} busy={busy} onRetry={(confirm) => decide({ approval, decision: 'retry', confirm })} />;
  }
  return (
    <div className={cn(bar, 'bg-bg-app')}>
      <span className="text-xs text-fg-muted">{footer.text}</span>
    </div>
  );
}

/** A send that did not go: Try again. One that broke off may have gone,
 *  so sending it again asks once more first. */
function FailedFooter({
  className,
  text,
  unknown,
  busy,
  onRetry,
}: {
  className: string;
  text: string;
  unknown: boolean;
  busy: boolean;
  onRetry: (confirm: boolean) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  if (confirming) {
    return (
      <div className={cn(className, 'bg-action-stop-soft')}>
        <span className="min-w-0 flex-1 text-xs text-fg-default" role="status">
          It may already have gone out. Send it again anyway?
        </span>
        <Button variant="quiet" size="sm" onClick={() => setConfirming(false)} className="font-semibold">
          Keep it
        </Button>
        <ActionButton
          intent="stop"
          disabled={busy}
          onClick={() => {
            setConfirming(false);
            onRetry(true);
          }}
        >
          Send again
        </ActionButton>
      </div>
    );
  }
  return (
    <div className={cn(className, 'bg-action-stop-soft')}>
      <span className="min-w-0 flex-1 text-xs text-fg-default" role="status">
        {text}
      </span>
      <Button
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() => (unknown ? setConfirming(true) : onRetry(false))}
        className="gap-1.5 font-semibold"
      >
        <RotateCcw aria-hidden className="size-3.25" />
        {unknown ? 'Send again' : 'Try again'}
      </Button>
    </div>
  );
}

export default ApprovalCard;
