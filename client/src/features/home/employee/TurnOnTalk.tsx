/**
 * An employee with no talk line yet that can be given one (talk `off`:
 * hired before Talk, or built in Dev mode). "Turn on Talk" adds the line
 * after current work finishes. Existing conversations and approvals remain.
 */

import { MessageSquare } from 'lucide-react';
import { useState } from 'react';
import { ActionButton } from '@/components/ui/action-button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import type { EmployeeSummary } from '../data/schemas';
import { useEnableTalk } from '../data/talk';
import { pillToast } from '../ui/pillToast';

function enableErrorMessage(code: string, name: string): string {
  switch (code) {
    case 'not_found':
      return 'This employee is no longer on the team.';
    case 'unsupported':
      return `Talk can’t be turned on for ${name}.`;
    case 'conflict':
      return `${name} is in the middle of a change. Try again in a moment.`;
    case 'apply_failed':
    case 'safe_apply_runtime_required':
      return `Talk is saved, but ${name} couldn’t start using it yet. Their current setup is still in place.`;
    default:
      return 'That did not work. Try again.';
  }
}

export function TurnOnTalk({ employee }: { employee: EmployeeSummary }) {
  const enable = useEnableTalk();
  const [confirming, setConfirming] = useState(false);
  const { name } = employee;

  const turnOn = () =>
    enable.mutate(employee.workflow_id, {
      onSuccess: (result) => pillToast(result.activation_state === 'waiting'
        ? `Talk will be ready after ${name} finishes current work.`
        : result.activation_state === 'saved' ? `Talk is saved. Start ${name} to use it.` : `Talk is on. Say hello to ${name}.`),
      onError: (error) => pillToast(enableErrorMessage(error.message, name), { tone: 'error' }),
    });

  return (
    <section
      aria-label={`Talk with ${name}`}
      className="flex w-full flex-col items-center gap-3 rounded-card border border-border-default bg-bg-panel px-5 py-6 text-center"
    >
      <MessageSquare aria-hidden className="size-5 text-fg-faint" strokeWidth={1.75} />
      <p className="m-0 text-md font-medium text-fg-default">Talk with {name}</p>
      <p className="m-0 max-w-100 text-sm text-fg-muted">Turn on Talk to send {name} messages and get answers right here.</p>
      <ActionButton intent="config" disabled={enable.isPending} onClick={() => setConfirming(true)} className="h-9 rounded-row px-4">
        {enable.isPending ? 'Turning on…' : 'Turn on Talk'}
      </ActionButton>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Turn on Talk for {name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Talk can wait for {name} to finish current work. Their conversations and drafts waiting for approval stay in place.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Not now</AlertDialogCancel>
            <AlertDialogAction onClick={turnOn}>Turn on Talk</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

export default TurnOnTalk;
