/**
 * Talking to an employee on their page: the shared chat (features/chat)
 * over the session whose id is their workflow id, with Home around it.
 *
 * - The orb stays above the conversation: the thread scrolls under it, so a
 *   long conversation never takes it out of view. A new hire's first-day
 *   strip and notes are the thread's first items. What they want to send
 *   waits for the owner on cards in the chat (features/chat), with the Ask
 *   first chip beside the box; the line under the box says whether they ask
 *   before sending anything.
 * - The message box follows their control state the way the server does: a
 *   message goes at once while they run, waits for Resume while they are
 *   paused, and cannot be sent otherwise. Above it, what to act on: Resume or
 *   Start (or connect what they are missing), Apply while saved changes wait
 *   for a restart, Help in browser while they wait for the owner there, and
 *   why a run of failures paused them.
 * - Without a talk line, Turn on Talk offers to add one; an employee whose
 *   setup cannot answer gets a note instead of the box.
 * - A new hire's page opens with their first-day strip (FirstDay) at the
 *   top of the conversation for the rest of the session: it says how their
 *   start is going and, until the first message, offers their main action
 *   when it did not go ahead (the line above the box then stays away, so it
 *   is not said twice) and lists their routine. After that message the strip
 *   can scroll out of view, so the line above the box is back. Before it,
 *   while they start, the box shows but takes nothing yet.
 * - A document they wrote opens from its card in the reply on the
 *   Workspace's Canvas tab, at that version.
 * - Cmd/Ctrl+K puts the cursor in the message box.
 */

import { Monitor } from 'lucide-react';
import { useCallback, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ChatPane, useChatThread, useLaneRun, type ChatPaneHandle, type ComposerMode } from '@/features/chat';
import { invalidateEmployees } from '../data/employees';
import { getDefaultEmployeeAvatar } from '../data/avatars';
import { firstDayPhase, stateNoticeText, talkMode, talkNoticeText } from '../data/presentation';
import type { EmployeeSummary } from '../data/schemas';
import { useRetryNote } from '../data/talk';
import { HireNotice } from '../hire/HireNotice';
import { ENERGY, SPIKE, setEnergyTarget, spikeOrb } from '../orb/orb';
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
import { LlmWaitNote } from '@/components/ui/LlmWaitNote';
import type { EmployeeControl } from './useEmployeeControl';

/** The agent is waiting for the owner in the browser (it called `request_user`). */
function BrowserNotice({ employee }: { employee: EmployeeSummary }) {
  const openWorkspace = useHomeStore((s) => s.openWorkspace);
  const setWorkspaceTab = useHomeStore((s) => s.setWorkspaceTab);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <p className="m-0 min-w-50 flex-1 text-sm text-fg-muted">{employee.task?.text || `${employee.name} needs you in the browser.`}</p>
      <Button
        variant="quiet"
        onClick={() => {
          openWorkspace(employee.workflow_id);
          setWorkspaceTab('browser');
        }}
        className="h-9 gap-2 rounded-row border-border-default px-3.5 font-semibold text-fg-default"
      >
        <Monitor aria-hidden className="size-3.5" />
        Help in browser
      </Button>
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
  /** Their first-day strip says their state and offers their main action
   *  (until the conversation has a message). */
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
      <LlmWaitNote workflowId={employee.workflow_id} />
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

  // Their first day lasts the session (homeStore.firstDays); its routine
  // line, its main action and the waiting box last until the conversation
  // has a message.
  const firstDay = useHomeStore((s) => s.firstDays[workflowId]);
  const spoken = (useChatThread(workflowId, 'all').data?.messages.length ?? 0) > 0;
  const phase = firstDay ? firstDayPhase(employee, firstDay.started) : null;

  const composer: ComposerMode =
    employee.talk.state !== 'on' ? 'closed' : phase === 'starting' && !spoken ? 'wait' : mode === 'start' ? 'closed' : mode;

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
  // Asked to take focus on arrival (a Get started row: the card goes with
  // the hire view, so focus would land on the page): the message box takes it.
  const focusNonce = useHomeStore((s) => s.composerFocus);
  const consumeComposerFocus = useHomeStore((s) => s.consumeComposerFocus);
  useEffect(() => {
    if (!focusNonce) return;
    pane.current?.focusComposer();
    consumeComposerFocus();
  }, [focusNonce, consumeComposerFocus]);

  // The orb answers the conversation (design handoff "Orb reactivity"): it
  // brightens while the owner writes, and spikes when something goes.
  const onSent = useCallback((what: 'message' | 'draft') => spikeOrb(what === 'draft' ? SPIKE.draftSent : SPIKE.message), []);
  const onComposerChange = useCallback(({ focused, typed }: { focused: boolean; typed: boolean }) => {
    setEnergyTarget(typed ? ENERGY.typing : focused ? ENERGY.focus : ENERGY.idle);
  }, []);
  useEffect(() => () => setEnergyTarget(ENERGY.idle), []);

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
    <>
      <div className="flex flex-none justify-center pt-7">
        <OrbSlot size="employee" />
      </div>
      <ChatPane
        ref={pane}
        host={{
          kind: 'home',
          sessionId: workflowId,
          scope: 'all',
          persona: { name, colorRole: employee.color_role, photo: employee.photo_url, fallbackPhoto: getDefaultEmployeeAvatar(workflowId) },
          composer,
          placeholder: composer === 'wait' ? `${name} is starting…` : undefined,
          notices: <Notices employee={employee} control={control} queued={lane?.state === 'queued'} firstDay={Boolean(firstDay) && !spoken} />,
          top: (
            <>
              {firstDay && phase && (
                <FirstDay employee={employee} firstDay={firstDay} phase={phase} control={control} spoken={spoken} />
              )}
              <HireNotice workflowId={workflowId} />
            </>
          ),
          footnote: employee.asks_first
            ? `${name} asks before sending anything on your behalf.`
            : `${name} doesn’t ask before sending anything on your behalf.`,
          notify: (message, tone) => pillToast(message, { tone }),
          openArtifact: (artifact) => useHomeStore.getState().openCanvasItem(artifact),
          onSendRefused,
          onSent,
          onComposerChange,
          modelPicker: true,
          liveNote,
          onScrolledChange,
        }}
      />
    </>
  );
}

export default EmployeeChat;
