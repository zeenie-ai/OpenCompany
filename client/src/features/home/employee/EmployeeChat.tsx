/**
 * Talking to an employee on their page: the shared chat (features/chat)
 * over the session whose id is their workflow id, with Home around it.
 *
 * - The thread scrolls under the header with the orb as its first item (and
 *   a new hire's notes under it); what they want to send waits for the
 *   owner on cards in the chat (features/chat), with the Ask first chip
 *   beside the box; the line under the box says whether they ask before
 *   sending anything.
 * - The message box follows their control state the way the server does: a
 *   message goes at once while they run, waits for Resume while they are
 *   paused, and cannot be sent otherwise. Above it, what to act on: Resume or
 *   Start (or connect what they are missing), Apply while saved changes wait
 *   for a restart, Help in browser while they wait for the owner there, and
 *   why a run of failures paused them.
 * - Without a talk line, Turn on Talk offers to add one; an employee whose
 *   setup cannot answer gets a note instead of the box.
 * - A new hire's page opens on their first day (FirstDay) until their first
 *   conversation begins: the box waits while they start, then offers
 *   something to say, and the card says why when the start did not go ahead
 *   (so the line above the box does not say it twice).
 * - A document they wrote opens from its card in the reply on the
 *   Workspace's Canvas tab, at that version.
 * - Cmd/Ctrl+K puts the cursor in the message box.
 */

import { Monitor } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ActionButton } from '@/components/ui/action-button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ChatPane, useChatThread, useLaneRun, type ChatPaneHandle, type ComposerMode } from '@/features/chat';
import { invalidateEmployees } from '../data/employees';
import { firstDayPhase, stateNoticeText, talkMode, talkNoticeText } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { useRetryNote } from '../data/talk';
import { HireNotice } from '../hire/HireNotice';
import { OrbSlot } from '../orb/OrbSlot';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';
import { PendingChangesNotice } from './PendingChangesNotice';
import { PrimaryActionButton } from './PrimaryActionButton';
import { TurnOnTalk } from './TurnOnTalk';
import { EmployeeAccess } from './EmployeeAccess';
import { GiveTeam } from './GiveTeam';
import { EmployeeWork } from './EmployeeWork';
import { FirstDay } from './FirstDay';
import type { EmployeeControl } from './useEmployeeControl';

/** What to say first, on a new hire's first day. */
const GREETINGS = ['Hi! What can you do?', 'Walk me through your routine'] as const;

/** The agent is waiting for the owner in the browser (it called `request_user`). */
function BrowserNotice({ employee }: { employee: EmployeeSummary }) {
  const openWorkspace = useHomeStore((s) => s.openWorkspace);
  const setWorkspaceTab = useHomeStore((s) => s.setWorkspaceTab);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p className="m-0 min-w-50 flex-1 text-sm text-fg-muted">{employee.task?.text || `${employee.name} needs you in the browser.`}</p>
      <ActionButton
        intent="tools"
        onClick={() => {
          openWorkspace(employee.workflow_id);
          setWorkspaceTab('browser');
        }}
        className="h-9 gap-2 rounded-row px-3.5"
      >
        <Monitor aria-hidden className="size-3.5" />
        Help in browser
      </ActionButton>
    </div>
  );
}

/** Why the circuit breaker paused them: the failure that kept repeating. */
function failurePauseText(employee: EmployeeSummary): string | null {
  if (employee.control.pause_reason !== 'failures') return null;
  return employee.control.pause_detail || (employee.task?.label === 'Paused' ? employee.task.text : null) || null;
}

function Notices({
  employee,
  control,
  queued,
  firstDay,
}: {
  employee: EmployeeSummary;
  control: EmployeeControl;
  queued: boolean;
  /** Their first day's card says their state and offers their main action. */
  firstDay: boolean;
}) {
  const { name } = employee;
  const mode = talkMode(employee.control);
  const failure = mode === 'queue' ? failurePauseText(employee) : null;
  // Their main action shows whether or not they can take messages here.
  const notice = firstDay ? null : employee.talk.state === 'on' ? talkNoticeText(mode, name, queued) : stateNoticeText(mode, name);
  return (
    <>
      {failure && (
        <Alert variant="destructive">
          <AlertDescription>{failure}</AlertDescription>
        </Alert>
      )}
      {employee.pending_changes && <PendingChangesNotice employee={employee} />}
      <EmployeeWork key={employee.workflow_id} employee={employee} />
      <GiveTeam workflowId={employee.workflow_id} available={employee.can_give_team} />
      <EmployeeAccess workflowId={employee.workflow_id} name={name} pendingOnly />
      {employee.browser_request && <BrowserNotice employee={employee} />}
      {employee.talk.state === 'off' && <TurnOnTalk employee={employee} />}
      {employee.talk.state === 'unsupported' && (
        <p className="m-0 w-full rounded-card border border-border-default bg-bg-panel px-4 py-3 text-center text-sm text-fg-muted">
          You can’t message {name} here. Their setup has no way to answer you.
        </p>
      )}
      {notice && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="m-0 min-w-50 flex-1 text-sm text-fg-muted">{notice}</p>
          <PrimaryActionButton control={control} />
        </div>
      )}
    </>
  );
}

export function EmployeeChat({
  employee,
  control,
  onScrolledChange,
}: {
  employee: EmployeeSummary;
  control: EmployeeControl;
  onScrolledChange?: (scrolled: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { workflow_id: workflowId, name } = employee;
  const lane = useLaneRun(workflowId);
  const liveNote = useRetryNote(workflowId, employee.talk.agent_node_id, lane !== null && lane.state !== 'queued');
  const mode = talkMode(employee.control);

  // Their first day lasts until their first conversation begins: a Reset
  // that empties the thread later does not bring it back.
  const firstDay = useHomeStore((s) => s.firstDays[workflowId]);
  const endFirstDay = useHomeStore((s) => s.endFirstDay);
  const spoken = (useChatThread(workflowId, 'all').data?.messages.length ?? 0) > 0;
  useEffect(() => {
    if (firstDay && spoken) endFirstDay(workflowId);
  }, [endFirstDay, firstDay, spoken, workflowId]);
  const phase = firstDay ? firstDayPhase(employee, firstDay.started) : null;

  const composer: ComposerMode =
    employee.talk.state !== 'on' ? 'closed' : phase === 'starting' ? 'wait' : mode === 'start' ? 'closed' : mode;

  // Cmd/Ctrl+K puts the cursor in the message box (on Home only: in the
  // editor it opens the command palette, which has Focus Chat).
  const pane = useRef<ChatPaneHandle>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== 'k' || !(event.metaKey || event.ctrlKey) || event.shiftKey || event.altKey) return;
      event.preventDefault();
      pane.current?.focusComposer();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const onSendRefused = (code: string) => {
    if (code === 'not_running') {
      invalidateEmployees(queryClient);
      pillToast(`${name} isn’t running. Start them first.`, { tone: 'error' });
    } else if (code === 'run_in_progress') {
      // One answer at a time: the server refuses a second message while
      // the employee is still working on the last one.
      pillToast(`${name} is still working on your last message. Send this once they answer.`, { tone: 'info' });
    } else {
      pillToast('Your message didn’t send. Try again.', { tone: 'error' });
    }
  };

  return (
    <ChatPane
      ref={pane}
      host={{
        kind: 'home',
        sessionId: workflowId,
        scope: 'all',
        persona: { name, colorRole: employee.color_role, photo: employee.photo_url },
        composer,
        placeholder: composer === 'wait' ? `${name} is starting…` : undefined,
        notices: <Notices employee={employee} control={control} queued={lane?.state === 'queued'} firstDay={Boolean(firstDay)} />,
        top: (
          <div className="flex flex-col items-center gap-4">
            <OrbSlot size="employee" />
            <HireNotice workflowId={workflowId} />
          </div>
        ),
        emptyState: firstDay && phase ? <FirstDay employee={employee} firstDay={firstDay} phase={phase} control={control} /> : undefined,
        greetings: firstDay ? GREETINGS : undefined,
        footnote: employee.asks_first
          ? `${name} asks before sending anything on your behalf.`
          : `${name} doesn’t ask before sending anything on your behalf.`,
        notify: (message, tone) => pillToast(message, { tone }),
        openArtifact: (artifact) => useHomeStore.getState().openCanvasItem(artifact),
        onSendRefused,
        liveNote,
        onScrolledChange,
      }}
    />
  );
}

export default EmployeeChat;
