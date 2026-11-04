/**
 * What an approval card says in each state (design handoff chat,
 * "Approval card"), as plain data so the copy is testable on its own:
 *
 * - pending (and editing): "{name} wants to send a WhatsApp message", Needs
 *   your OK, Decline / Edit / Always allow / Send (Allow for a call with no
 *   message to show);
 * - approved, inside the Undo window: Sending, "Sends in Ns" with Undo (none
 *   for a call its turn waits on);
 * - approved while its employee is stopped: Waiting, muted as paused, "Sends
 *   when you resume {name}.";
 * - sending: Sending, a spinner;
 * - sent (and a gate's draft once it went through): Sent, with when and to
 *   whom, and whether it went without asking;
 * - discarded: Declined, with what the owner said, and Restore while it can;
 * - failed: not sent, or may have gone (the call broke off), with Try again;
 * - expired, cancelled: why it never went;
 * - an `effect` row (a call that broke off midway, not a send): "{name}
 *   tried to …", May have happened, with Dismiss only; Dismissed once
 *   dismissed.
 *
 * A card with no message to show (a tool with no spec, or a call that sends
 * no message) previews nothing and says "done" or "run" where a message
 * says "sent". Copy says what really happens: a draft is "sent" only once it
 * went.
 */

import { timeLabel } from '../thread/timeLabel';
import { secondsLeft, type ChatApproval } from '../data/approvals';

export type CardTone = 'waiting' | 'sending' | 'sent' | 'muted' | 'failed';

export type CardFooter =
  | { kind: 'decide' }
  | { kind: 'undo'; seconds: number }
  | { kind: 'sending' }
  /** Approved, and it waits for Resume before it goes; a draft can still be undone. */
  | { kind: 'waiting'; text: string; undo: boolean }
  | { kind: 'sent'; text: string }
  /** Declined, with what the owner said, if anything. */
  | { kind: 'discarded'; text: string; note: string | null; restoreSeconds: number | null; canRestore: boolean }
  | { kind: 'failed'; text: string; unknown: boolean }
  /** It broke off midway: the owner checks, then dismisses it. */
  | { kind: 'effect'; text: string }
  | { kind: 'ended'; text: string };

export interface CardView {
  title: string;
  sub: string;
  pill: { label: string; tone: CardTone; pulse: boolean };
  /** The card's frame: a ring while it waits for the owner. */
  tone: CardTone;
  /** What the card previews: the message as it goes, or nothing (its lines
   *  say what the call runs with). */
  preview: 'message' | 'none';
  /** How the message preview reads. */
  bubble: 'draft' | 'sent' | 'struck';
  /** The small line inside the preview. */
  when: string;
  footer: CardFooter;
}

function lowerFirst(text: string): string {
  return text ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

export interface ViewOptions {
  name: string;
  nowMs: number;
  offsetMs: number;
  editing?: boolean;
  now?: Date;
}

export function approvalView(approval: ChatApproval, { name, nowMs, offsetMs, editing = false, now = new Date(nowMs) }: ViewOptions): CardView {
  const to = approval.recipientLabel || approval.recipient || 'them';
  const channel = approval.channel || 'the app';
  if (approval.kind === 'effect') return effectView(approval, name, channel, now);
  const drafted = timeLabel(approval.createdAt, now);
  const sub = drafted ? `${channel} · drafted ${/\d/.test(drafted.charAt(0)) ? 'at ' : ''}${drafted}` : channel;
  const noun = approval.outcomeLabels;
  const action = approval.action ? lowerFirst(approval.action) : 'send a message';
  const preview = hasMessage(approval) ? 'message' : 'none';
  const message = preview === 'message';

  switch (approval.status) {
    case 'pending':
      return {
        title: `${name} wants to ${action}`,
        sub,
        pill: editing ? { label: 'Editing', tone: 'waiting', pulse: false } : { label: 'Needs your OK', tone: 'waiting', pulse: true },
        tone: 'waiting',
        preview,
        bubble: 'draft',
        when: 'Draft',
        footer: { kind: 'decide' },
      };
    case 'approved': {
      if (approval.consumedAt === null) {
        const seconds = secondsLeft(approval.undoUntil, offsetMs, nowMs);
        // Nothing goes while its employee is stopped: it waits for Resume. A
        // draft can be undone until its send starts; a call its turn waits
        // on cannot.
        const paused =
          approval.deploymentState === 'paused' || approval.deploymentState === 'pausing' || approval.deploymentState === 'resuming';
        const footer: CardFooter =
          seconds > 0 ? { kind: 'undo', seconds }
          : paused ? { kind: 'waiting', text: `${message ? 'Sends' : 'Runs'} when you resume ${name}.`, undo: approval.kind === 'tool_call' && !approval.waits }
          : { kind: 'sending' };
        // Held for Resume, it reads as paused: the muted pill, still.
        const held = paused && seconds === 0;
        return {
          title: `${name} is ${message ? 'sending' : 'doing'} it`,
          sub,
          pill: held ? { label: 'Waiting', tone: 'muted', pulse: false } : { label: message ? 'Sending' : 'Running', tone: 'sending', pulse: true },
          tone: 'sending',
          preview,
          bubble: 'draft',
          when: 'Sending',
          footer,
        };
      }
      const at = timeLabel(approval.consumedAt, now);
      return sentView(approval, noun.sent, sub, to, channel, at, preview);
    }
    case 'sending':
      return {
        title: `${name} is ${message ? 'sending' : 'doing'} it`,
        sub,
        pill: { label: message ? 'Sending' : 'Running', tone: 'sending', pulse: true },
        tone: 'sending',
        preview,
        bubble: 'draft',
        when: 'Sending',
        footer: { kind: 'sending' },
      };
    case 'sent':
      return sentView(approval, noun.sent, sub, to, channel, timeLabel(approval.outcome?.at ?? approval.consumedAt, now), preview);
    case 'discarded': {
      const restoreSeconds = approval.kind === 'gate' ? secondsLeft(approval.restoreUntil, offsetMs, nowMs) : null;
      const canRestore = approval.kind === 'gate' ? (restoreSeconds ?? 0) > 0 : secondsLeft(approval.restoreUntil, offsetMs, nowMs) > 0;
      return {
        title: 'Declined',
        sub,
        pill: { label: 'Declined', tone: 'muted', pulse: false },
        tone: 'muted',
        preview,
        bubble: 'struck',
        when: 'Not sent',
        footer: { kind: 'discarded', text: `Declined. ${name} won’t ${message ? 'send' : 'do'} this.`, note: approval.note, restoreSeconds, canRestore },
      };
    }
    case 'failed': {
      const unknown = approval.outcome?.certainty === 'unknown';
      const why = approval.outcome?.error ? `: ${approval.outcome.error}` : '.';
      const label = unknown ? (message ? 'May have sent' : 'May have happened') : message ? 'Didn’t send' : 'Didn’t run';
      const text = unknown ? `It may have ${message ? 'gone out' : 'happened'}${why}` : `It didn’t ${message ? 'go out' : 'run'}${why}`;
      return {
        title: noun.failed,
        sub,
        pill: { label, tone: 'failed', pulse: false },
        tone: 'failed',
        preview,
        bubble: 'draft',
        when: 'Not sent',
        footer: { kind: 'failed', text, unknown },
      };
    }
    case 'expired':
      return endedView(
        message ? 'Draft expired' : 'Request expired',
        'Expired',
        sub,
        `It waited too long, so it ${message ? 'was not sent' : 'did not run'}.`,
        preview,
      );
    case 'cancelled':
      return endedView(
        message ? 'Draft cancelled' : 'Request cancelled',
        'Cancelled',
        sub,
        `Not ${message ? 'sent' : 'run'}: the conversation it belonged to ended.`,
        preview,
      );
  }
}

/** Whether the card has a message to preview: a gate's draft, or a spec'd
 *  call with a message or an editable one. */
function hasMessage(approval: ChatApproval): boolean {
  return approval.kind === 'gate' || (!approval.generic && (approval.editable || approval.body !== ''));
}

function sentView(approval: ChatApproval, title: string, sub: string, to: string, channel: string, at: string, preview: CardView['preview']): CardView {
  const when = at ? ` at ${at}` : '';
  const auto = approval.approvedBy === 'auto' ? ', without asking' : '';
  return {
    title,
    sub,
    pill: { label: preview === 'message' ? 'Sent' : 'Done', tone: 'sent', pulse: false },
    tone: 'sent',
    preview,
    bubble: 'sent',
    when: at || 'Sent',
    footer: { kind: 'sent', text: preview === 'message' ? `Sent to ${to} on ${channel}${when}${auto}.` : `Done${when}${auto}.` },
  };
}

/** A call that broke off midway, so it may have happened. Nothing runs it
 *  again from here: the owner checks, then dismisses the card. */
function effectView(approval: ChatApproval, name: string, channel: string, now: Date): CardView {
  const at = timeLabel(approval.createdAt, now);
  const sub = at ? `${channel} · ${/\d/.test(at.charAt(0)) ? 'at ' : ''}${at}` : channel;
  const title = `${name} tried to ${approval.action ? lowerFirst(approval.action) : `use ${channel}`}`;
  if (approval.status === 'cancelled') return endedView(title, 'Dismissed', sub, 'Dismissed.', 'none');
  const error = approval.outcome?.error;
  return {
    title,
    sub,
    pill: { label: 'May have happened', tone: 'failed', pulse: false },
    tone: 'failed',
    preview: 'none',
    bubble: 'draft',
    when: '',
    footer: { kind: 'effect', text: `It broke off midway${error ? ` (${error})` : ''}, so it may have happened.` },
  };
}

function endedView(title: string, label: string, sub: string, text: string, preview: CardView['preview']): CardView {
  return {
    title,
    sub,
    pill: { label, tone: 'muted', pulse: false },
    tone: 'muted',
    preview,
    bubble: 'struck',
    when: 'Not sent',
    footer: { kind: 'ended', text },
  };
}

/** The class pill of a request card (RFC-0007 §4.5), or null: a read never
 *  asks. */
export function classLabel(approval: ChatApproval): string | null {
  switch (approval.approvalClass) {
    case 'write':
      return 'Makes changes';
    case 'send':
      return 'Reaches out';
    case 'money':
      return 'Money or deletes';
    default:
      return null;
  }
}

/** Whether the card needs a tick every second (a countdown on screen). */
export function needsTicks(view: CardView): boolean {
  return view.footer.kind === 'undo' || (view.footer.kind === 'discarded' && view.footer.restoreSeconds !== null && view.footer.canRestore);
}
