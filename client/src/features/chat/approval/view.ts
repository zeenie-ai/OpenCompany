/**
 * What an approval card says in each state (design handoff chat,
 * "Approval card"), as plain data so the copy is testable on its own:
 *
 * - pending (and editing): "{name} wants to send a WhatsApp message", Needs
 *   your OK, Discard / Edit / Send;
 * - approved, inside the Undo window: Sending, "Sends in Ns" with Undo;
 * - sending: Sending, a spinner;
 * - sent (and a gate's draft once it went through): Sent, with when and to
 *   whom, and whether Ask first was off;
 * - discarded: Discarded, with Restore while it can;
 * - failed: not sent, or may have gone (the call broke off), with Try again;
 * - expired, cancelled: why it never went.
 *
 * Copy says what really happens: a draft is "sent" only once it went.
 */

import { timeLabel } from '../thread/timeLabel';
import { secondsLeft, type ChatApproval } from '../data/approvals';

export type CardTone = 'waiting' | 'sending' | 'sent' | 'muted' | 'failed';

export type CardFooter =
  | { kind: 'decide' }
  | { kind: 'undo'; seconds: number }
  | { kind: 'sending' }
  /** Approved, and its run waits for Resume before it goes. */
  | { kind: 'waiting'; text: string }
  | { kind: 'sent'; text: string }
  | { kind: 'discarded'; text: string; restoreSeconds: number | null; canRestore: boolean }
  | { kind: 'failed'; text: string; unknown: boolean }
  | { kind: 'ended'; text: string };

export interface CardView {
  title: string;
  sub: string;
  pill: { label: string; tone: CardTone; pulse: boolean };
  /** The card's frame: a ring while it waits for the owner. */
  tone: CardTone;
  /** How the message preview reads. */
  bubble: 'draft' | 'sent' | 'struck';
  /** The small line inside the preview. */
  when: string;
  footer: CardFooter;
}

function lowerFirst(text: string): string {
  return text ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

function nouns(approval: ChatApproval): { sent: string; failed: string } {
  const action = (approval.action ?? '').toLowerCase();
  if (action.includes('email')) return { sent: 'Email sent', failed: 'Email not sent' };
  if (action.includes('invite')) return { sent: 'Invites sent', failed: 'Invites not sent' };
  if (action.includes('share')) return { sent: 'File shared', failed: 'File not shared' };
  return { sent: 'Message sent', failed: 'Message not sent' };
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
  const drafted = timeLabel(approval.createdAt, now);
  const sub = drafted ? `${channel} · drafted ${/\d/.test(drafted.charAt(0)) ? 'at ' : ''}${drafted}` : channel;
  const noun = nouns(approval);
  const action = approval.action ? lowerFirst(approval.action) : 'send a message';

  switch (approval.status) {
    case 'pending':
      return {
        title: `${name} wants to ${action}`,
        sub,
        pill: editing ? { label: 'Editing', tone: 'waiting', pulse: false } : { label: 'Needs your OK', tone: 'waiting', pulse: true },
        tone: 'waiting',
        bubble: 'draft',
        when: 'Draft',
        footer: { kind: 'decide' },
      };
    case 'approved': {
      if (approval.consumedAt === null) {
        const seconds = secondsLeft(approval.undoUntil, offsetMs, nowMs);
        const paused = approval.kind === 'gate' && (approval.deploymentState === 'paused' || approval.deploymentState === 'pausing');
        const footer: CardFooter =
          seconds > 0 ? { kind: 'undo', seconds } : paused ? { kind: 'waiting', text: `Sends when you resume ${name}.` } : { kind: 'sending' };
        return {
          title: `${name} is sending it`,
          sub,
          pill: { label: paused && seconds === 0 ? 'Waiting' : 'Sending', tone: 'sending', pulse: !(paused && seconds === 0) },
          tone: 'sending',
          bubble: 'draft',
          when: 'Sending',
          footer,
        };
      }
      const at = timeLabel(approval.consumedAt, now);
      return sentView(approval, noun.sent, sub, to, channel, at);
    }
    case 'sending':
      return {
        title: `${name} is sending it`,
        sub,
        pill: { label: 'Sending', tone: 'sending', pulse: true },
        tone: 'sending',
        bubble: 'draft',
        when: 'Sending',
        footer: { kind: 'sending' },
      };
    case 'sent':
      return sentView(approval, noun.sent, sub, to, channel, timeLabel(approval.outcome?.at ?? approval.consumedAt, now));
    case 'discarded': {
      const restoreSeconds = approval.kind === 'gate' ? secondsLeft(approval.restoreUntil, offsetMs, nowMs) : null;
      const canRestore = approval.kind === 'gate' ? (restoreSeconds ?? 0) > 0 : secondsLeft(approval.restoreUntil, offsetMs, nowMs) > 0;
      return {
        title: 'Draft discarded',
        sub,
        pill: { label: 'Discarded', tone: 'muted', pulse: false },
        tone: 'muted',
        bubble: 'struck',
        when: 'Not sent',
        footer: { kind: 'discarded', text: `Discarded. ${name} won’t send this.`, restoreSeconds, canRestore },
      };
    }
    case 'failed': {
      const unknown = approval.outcome?.certainty === 'unknown';
      const why = approval.outcome?.error ? `: ${approval.outcome.error}` : '.';
      return {
        title: noun.failed,
        sub,
        pill: { label: unknown ? 'May have sent' : 'Didn’t send', tone: 'failed', pulse: false },
        tone: 'failed',
        bubble: 'draft',
        when: 'Not sent',
        footer: { kind: 'failed', text: unknown ? `It may have gone out${why}` : `It didn’t go out${why}`, unknown },
      };
    }
    case 'expired':
      return endedView('Draft expired', 'Expired', sub, 'It waited too long, so it was not sent.');
    case 'cancelled':
      return endedView('Draft cancelled', 'Cancelled', sub, 'Not sent: the conversation it belonged to ended.');
  }
}

function sentView(approval: ChatApproval, title: string, sub: string, to: string, channel: string, at: string): CardView {
  const when = at ? ` at ${at}` : '';
  const auto = approval.approvedBy === 'auto' ? ', without asking: Ask first was off' : '';
  return {
    title,
    sub,
    pill: { label: 'Sent', tone: 'sent', pulse: false },
    tone: 'sent',
    bubble: 'sent',
    when: at || 'Sent',
    footer: { kind: 'sent', text: `Sent to ${to} on ${channel}${when}${auto}.` },
  };
}

function endedView(title: string, label: string, sub: string, text: string): CardView {
  return {
    title,
    sub,
    pill: { label, tone: 'muted', pulse: false },
    tone: 'muted',
    bubble: 'struck',
    when: 'Not sent',
    footer: { kind: 'ended', text },
  };
}

/** Whether the card needs a tick every second (a countdown on screen). */
export function needsTicks(view: CardView): boolean {
  return view.footer.kind === 'undo' || (view.footer.kind === 'discarded' && view.footer.restoreSeconds !== null && view.footer.canRestore);
}
