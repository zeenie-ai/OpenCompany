/**
 * The bar under a finished answer (design handoff chat, "Assistant row"):
 * Copy, Good and Bad (a rating the employee reads before its next answer;
 * pressing it again takes it back), Try again on the latest answer, ‹ 1 / 2
 * › between the answers tried for the same message, what the reply's run
 * cost (an estimate of its model and tool calls, when the server booked
 * any), and when it was written. Faint until the turn is hovered or focused,
 * except under the latest answer. Rating, Try again and the cost need the
 * chat's TurnActions; Copy always works.
 */

import { Check, Copy, RotateCcw, ThumbsDown, ThumbsUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useChatRunCostsQuery } from '@/hooks/useUsageQueries';
import { cn } from '@/lib/utils';
import type { ChatMessage, Feedback } from '../data/schemas';
import { timeLabel } from '../thread/timeLabel';
import { useTurnActions } from '../thread/turnActions';
import { costLabel } from './runCopy';
import { TurnMeta } from './TurnMeta';
import { useCopied } from './useCopied';
import { VersionStepper } from './VersionStepper';

function RateButton({
  value,
  current,
  disabled,
  onRate,
}: {
  value: Feedback;
  current: Feedback | null;
  disabled: boolean;
  onRate: (value: Feedback | null) => void;
}) {
  const on = current === value;
  const Icon = value === 'up' ? ThumbsUp : ThumbsDown;
  const label = value === 'up' ? 'Good reply' : 'Bad reply';
  return (
    <Button
      variant="quiet"
      size="icon-sm"
      disabled={disabled}
      onClick={() => onRate(on ? null : value)}
      aria-label={label}
      aria-pressed={on}
      title={label}
      className={cn(on && 'text-fg-default')}
    >
      <Icon aria-hidden className="size-3.5" strokeWidth={1.9} fill={on ? 'currentColor' : 'none'} />
    </Button>
  );
}

export function ReplyActions({ message, latest, now }: { message: ChatMessage; latest: boolean; now: Date }) {
  const actions = useTurnActions();
  const [copied, copy] = useCopied();
  const costs = useChatRunCostsQuery(actions?.sessionId ?? '');
  const cost = costLabel(costs.data, message.runId);
  // An answer a run gave can be rated; a note from OpenCompany cannot.
  const rateable = Boolean(actions && message.runId && message.kind === 'text');
  return (
    <TurnMeta shown={latest} className="-ml-1.5 h-7 gap-0.5 px-0">
      {message.text.trim() && (
        <Button
          variant="quiet"
          size="icon-sm"
          onClick={() => copy(message.text)}
          aria-label={copied ? 'Copied' : 'Copy reply'}
          title={copied ? 'Copied' : 'Copy'}
        >
          {copied ? (
            <Check aria-hidden className="size-3.5 text-action-run-ink" strokeWidth={1.9} />
          ) : (
            <Copy aria-hidden className="size-3.5" strokeWidth={1.9} />
          )}
        </Button>
      )}
      {rateable && actions && (
        <>
          <RateButton value="up" current={message.feedback} disabled={false} onRate={(value) => actions.rate(message.id, value)} />
          <RateButton value="down" current={message.feedback} disabled={false} onRate={(value) => actions.rate(message.id, value)} />
        </>
      )}
      {message.editable && actions && (
        <Button
          variant="quiet"
          size="icon-sm"
          disabled={actions.busy}
          onClick={() => actions.regenerate(message.id)}
          aria-label="Try again"
          title="Try again"
        >
          <RotateCcw aria-hidden className="size-3.5" strokeWidth={1.9} />
        </Button>
      )}
      {message.siblings && actions && (
        <VersionStepper siblings={message.siblings} noun="reply" disabled={actions.busy} onSwitch={actions.switchTo} />
      )}
      {cost && (
        <span className="px-1.5 tabular-nums" title="Estimated cost of model and tool calls, including work handed to the team">
          {cost}
        </span>
      )}
      <span className="px-1.5">{timeLabel(message.timestamp, now)}</span>
    </TurnMeta>
  );
}
