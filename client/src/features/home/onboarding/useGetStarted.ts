/**
 * The Get started checklist's state (onboarding handoff D), from what Home
 * already knows:
 *
 * - Connect an AI model: one is connected now.
 * - Hire your first employee: someone on the team was hired.
 * - Say hello: the owner has written to their first hire. Latched in the
 *   owner's settings (`getting_started_said_hello`), since New conversation
 *   or a restart empties the thread.
 * - Approve a first draft: the owner sent a draft an employee held for them
 *   (`approval_lifecycle` decided as approved, heard while Home is open).
 *   Latched (`getting_started_approved_draft`).
 *
 * Each latch is written once: the saved flag guards it. The card shows once
 * the Welcome guide is finished, until the owner hides it (Settings > Help
 * shows it again).
 */

import { useEffect } from 'react';
import type { LucideIcon } from 'lucide-react';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { useChatThread } from '@/features/chat';
import { useSaveUserSettingsMutationCore } from '@/hooks/useUserSettingsQuery';
import { isConnected, useConnectors } from '../data/connectors';
import { useEmployeesQuery } from '../data/employees';
import { useOwnerSettings } from '../data/profile';
import type { EmployeeSummary } from '../data/schemas';
import { useHomeStore } from '../state/homeStore';
import { GET_STARTED_ITEMS, type GetStartedItemId } from './getStartedItems';

const SAID_HELLO = 'getting_started_said_hello';
const APPROVED_DRAFT = 'getting_started_approved_draft';

export interface GetStartedRow {
  id: GetStartedItemId;
  label: string;
  icon: LucideIcon;
  /** Under the label: what to do, or what was done. */
  sub: string;
  done: boolean;
  /** Where a click on a step not done yet takes the owner. */
  act?: () => void;
}

/** The employee hired first. */
function firstHire(team: readonly EmployeeSummary[]): EmployeeSummary | null {
  let first: EmployeeSummary | null = null;
  for (const employee of team) {
    if (employee.derived || !employee.hired_at) continue;
    if (!first || Date.parse(employee.hired_at) < Date.parse(first.hired_at ?? '')) first = employee;
  }
  return first;
}

export function useGetStarted() {
  const { data: settings, isSuccess } = useOwnerSettings();
  const { sendRequest, addEventListener } = useWebSocketActions();
  const { mutate: save } = useSaveUserSettingsMutationCore(sendRequest);
  const { providers, hasAi } = useConnectors();
  const team = useEmployeesQuery().data;
  const first = firstHire(team ?? []);

  const saidHello = Boolean(settings?.[SAID_HELLO]);
  const approved = Boolean(settings?.[APPROVED_DRAFT]);
  // Their first hire's conversation, read only until the owner has written to them.
  const thread = useChatThread(saidHello ? null : (first?.workflow_id ?? null), 'all');
  const wroteToFirst = Boolean(thread.data?.messages.some((message) => message.role === 'user'));

  useEffect(() => {
    if (isSuccess && wroteToFirst && !saidHello) save({ [SAID_HELLO]: true });
  }, [isSuccess, wroteToFirst, saidHello, save]);

  useEffect(() => {
    if (!isSuccess || approved) return;
    return addEventListener('approval_lifecycle', (event) => {
      if (event?.type === 'com.opencompany.approval.decided' && event?.data?.status === 'approved') save({ [APPROVED_DRAFT]: true });
    });
  }, [isSuccess, approved, addEventListener, save]);

  const ai = providers.find((provider) => provider.consumer_category === 'ai' && isConnected(provider));
  const waiting = (team ?? []).find((employee) => employee.pending_approvals > 0) ?? first;
  const home = useHomeStore.getState;
  const state: Record<GetStartedItemId, Omit<GetStartedRow, 'id' | 'label' | 'icon'>> = {
    'connect-ai': {
      done: hasAi,
      sub: ai ? `${ai.name} is connected` : 'OpenAI, Anthropic, Gemini or a local model',
      act: () => home().openGuide('connect'),
    },
    hire: {
      done: first !== null,
      sub: first ? `${first.name}, ${first.role}` : 'Describe a job or pick a starter',
      act: () => home().showHire({ focus: true }),
    },
    'say-hello': {
      done: saidHello || wroteToFirst,
      sub: first ? `Send ${first.name} a message` : 'Send your first hire a message',
      act: first ? () => home().showEmployee(first.workflow_id) : undefined,
    },
    'approve-draft': {
      done: approved,
      sub: 'They ask before sending anything',
      act: waiting ? () => home().showEmployee(waiting.workflow_id) : undefined,
    },
  };
  const rows: GetStartedRow[] = GET_STARTED_ITEMS.map((item) => ({ ...item, ...state[item.id] }));

  return {
    visible: isSuccess && Boolean(settings?.onboarding_completed) && !settings?.getting_started_dismissed,
    rows,
    doneCount: rows.filter((row) => row.done).length,
    dismiss: () => save({ getting_started_dismissed: true }),
  };
}

/** Settings > Help: show the hidden checklist again. */
export function useShowGetStarted(): () => void {
  const { sendRequest } = useWebSocketActions();
  const { mutate: save } = useSaveUserSettingsMutationCore(sendRequest);
  return () => save({ getting_started_dismissed: false });
}
