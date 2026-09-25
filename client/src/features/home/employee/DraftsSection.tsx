/**
 * "Waiting for you" on the employee card: every draft the employee holds
 * for approval, as the message will go out, with Discard, Edit and Send.
 *
 * Send delivers the draft (or the edited text) through the channel it
 * names; while the employee is paused it goes out when they resume.
 * An edit must fit the channel's limit and cannot be empty.
 */

import { useState } from 'react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { useApprovalsQuery, useDecideApproval, type Approval } from '../approvals/data';
import { DraftMessagePreview } from '../genui';
import { MicroLabel } from '../ui/primitives';
import { pillToast } from '../ui/pillToast';

function DraftCard({ approval, employeeName, paused }: { approval: Approval; employeeName: string; paused: boolean }) {
  const decide = useDecideApproval();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(approval.body);
  const trimmed = text.trim();
  const tooLong = trimmed.length > approval.max_length;
  const busy = decide.isPending;

  const send = () =>
    decide.mutate(
      { approval, decision: 'send', text: editing ? trimmed : undefined },
      {
        onSuccess: (result) =>
          pillToast(
            result.will_send_on_resume ? `Sends when you resume ${employeeName}` : `Sent to ${approval.recipient_label || approval.recipient}`,
          ),
      },
    );

  return (
    <div data-approval={approval.approval_id} className="flex flex-col gap-2.5">
      {approval.context_excerpt && (
        <p className="m-0 line-clamp-2 text-sm text-fg-muted">
          <span className="font-medium text-fg-default">{approval.recipient_label || approval.recipient}:</span> {approval.context_excerpt}
        </p>
      )}
      {editing ? (
        <div className="flex flex-col gap-1.5">
          <Textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            aria-label="Edit the draft"
            rows={4}
            className="min-h-24 resize-y bg-bg-app"
          />
          <span className={cn('self-end font-mono text-2xs', tooLong ? 'text-status-attention-ink' : 'text-fg-muted')}>
            {trimmed.length} / {approval.max_length}
          </span>
        </div>
      ) : (
        <DraftMessagePreview
          channel={approval.channel_label}
          to={approval.recipient_label || approval.recipient}
          subject={approval.subject}
          body={approval.body}
        />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="quiet"
          disabled={busy}
          onClick={() => decide.mutate({ approval, decision: 'discard' }, { onSuccess: () => pillToast('Draft discarded', { tone: 'info' }) })}
          className="h-8.5 rounded-row border-border-default px-3.5 font-semibold text-fg-default"
        >
          Discard
        </Button>
        <Button
          variant="quiet"
          disabled={busy}
          onClick={() => {
            setEditing((on) => !on);
            setText(approval.body);
          }}
          className="h-8.5 rounded-row border-border-default px-3.5 font-semibold text-fg-default"
        >
          {editing ? 'Cancel edit' : 'Edit'}
        </Button>
        <ActionButton intent="run" disabled={busy || !trimmed || tooLong} onClick={send} className="ml-auto h-8.5 rounded-row px-4">
          {busy ? 'Sending…' : 'Send'}
        </ActionButton>
      </div>
      {paused && <p className="m-0 text-xs text-fg-muted">Sends when you resume {employeeName}.</p>}
    </div>
  );
}

export function DraftsSection({ workflowId, employeeName, paused }: { workflowId: string; employeeName: string; paused: boolean }) {
  const { data: approvals } = useApprovalsQuery(workflowId);
  if (!approvals || approvals.length === 0) return null;
  return (
    <section aria-label="Waiting for you" className="flex flex-col gap-3 rounded-card border border-status-waiting-border bg-status-waiting-fill p-4">
      <MicroLabel className="text-status-waiting-ink">Waiting for you</MicroLabel>
      <div className="flex flex-col gap-5">
        {approvals.map((approval) => (
          <DraftCard key={approval.approval_id} approval={approval} employeeName={employeeName} paused={paused} />
        ))}
      </div>
    </section>
  );
}

export default DraftsSection;
