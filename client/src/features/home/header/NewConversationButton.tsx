/**
 * New conversation, in the header of an employee's page (design handoff
 * chat, "Header"). It clears the conversation and what the employee
 * remembers of it (`clear_chat_messages`), so it asks first. A new hire's
 * first day ends with it, as it does with their first message.
 */

import { SquarePen } from 'lucide-react';
import { useState } from 'react';
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
import { Button } from '@/components/ui/button';
import { useClearChat } from '@/features/chat';
import type { EmployeeSummary } from '../data/schemas';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

export function NewConversationButton({ employee }: { employee: EmployeeSummary }) {
  const [confirming, setConfirming] = useState(false);
  const clear = useClearChat(employee.workflow_id);
  const { name } = employee;

  const start = () =>
    clear.mutate(undefined, {
      onSuccess: () => useHomeStore.getState().endFirstDay(employee.workflow_id),
      onError: () => pillToast('That did not work. Try again.', { tone: 'error' }),
    });

  return (
    <>
      <Button
        variant="quiet"
        onClick={() => setConfirming(true)}
        disabled={clear.isPending}
        title="New conversation"
        aria-label="New conversation"
        className="h-8 gap-1.75 rounded-lg px-2.5 text-sm"
      >
        <SquarePen aria-hidden className="size-4" strokeWidth={1.75} />
        <span className="hidden lg:inline">New conversation</span>
      </Button>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Start a new conversation with {name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This clears the conversation, and {name} forgets it too. Drafts from it still waiting for you are dropped.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Not now</AlertDialogCancel>
            <AlertDialogAction onClick={start}>New conversation</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export default NewConversationButton;
