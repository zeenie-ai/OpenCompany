/**
 * "Hire now": a starter's skills go into the library first, then its own
 * setup is hired under a name nobody on the team has, and the new
 * employee's page opens. A skill that is not there, or a hire that fails,
 * says so and frees the hire slot; the failed hire keeps its key for the
 * retry.
 */

import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { UserSkill } from '@/hooks/useUserSkills';

let library: UserSkill[] = [];
let builtIns: Array<{ name: string; description: string; metadata: Record<string, unknown> }> = [];
let hireResponse: unknown = {};

const sendRequest = vi.fn(async (type: string, data: Record<string, any> = {}) => {
  switch (type) {
    case 'get_user_skills':
      return { skills: library };
    case 'scan_skill_folder':
      return { success: true, skills: builtIns };
    case 'get_skill_content':
      return { success: true, instructions: `Do ${data.skill_name}.` };
    case 'create_user_skill':
      library = [...library, row(data.name)];
      return { skill: library.at(-1) };
    case 'update_user_skill':
      library = library.map((entry) => (entry.name === data.name ? { ...entry, is_active: data.is_active } : entry));
      return { skill: library.find((entry) => entry.name === data.name) };
    case 'hire_employee':
      return hireResponse;
    default:
      return {};
  }
});

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('../../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { EMPLOYEES_QUERY_KEY } from '../../data/employees';
import { STARTERS } from '../../hire/templates';
import { useHomeStore } from '../../state/homeStore';
import { pillToast } from '../../ui/pillToast';
import { resetDraftForTests, useDraftStore } from '../draftStore';
import { useStarterHire } from '../useStarterHire';

const receptionist = STARTERS.find((starter) => starter.id === 'receptionist')!;

function row(name: string, patch: Partial<UserSkill> = {}): UserSkill {
  return { name, display_name: name, description: '', instructions: 'x', icon: '', color: '', category: 'custom', is_active: true, ...patch };
}

function setup(team: string[] = []) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(
    EMPLOYEES_QUERY_KEY,
    team.map((name, index) => ({ workflow_id: `t${index}`, name, revision: 1 })),
  );
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return renderHook(() => useStarterHire(), { wrapper });
}

async function hire(result: { current: ReturnType<typeof useStarterHire> }): Promise<boolean> {
  let hired = false;
  await act(async () => {
    hired = await result.current.hire(receptionist);
  });
  return hired;
}

beforeEach(() => {
  sendRequest.mockClear();
  vi.mocked(pillToast).mockClear();
  resetDraftForTests();
  useHomeStore.setState({ view: { kind: 'hire' }, hireNotice: null, firstDays: {} });
  library = [row('write-like-a-person', { is_active: false })];
  builtIns = receptionist.skills.map((name) => ({ name, description: `Use for ${name}.`, metadata: { title: name } }));
  hireResponse = {
    success: true,
    started: true,
    node_count: 12,
    warnings: [],
    employee: { workflow_id: 'w1', name: 'Rosa', role: 'Receptionist', status: 'working', control: {}, revision: 1 },
  };
});

describe('useStarterHire', () => {
  it('adds the skills, hires the starter under a free name, and opens them', async () => {
    useDraftStore.setState({ input: receptionist.job });
    const { result } = setup([receptionist.hire.names[0]]);
    expect(await hire(result)).toBe(true);

    expect(sendRequest).toHaveBeenCalledWith('update_user_skill', { name: 'write-like-a-person', is_active: true });
    expect(sendRequest).toHaveBeenCalledWith('create_user_skill', expect.objectContaining({ name: 'reply-in-their-language' }));
    const [, payload = {}] = sendRequest.mock.calls.find(([type]) => type === 'hire_employee')!;
    expect(payload).toMatchObject({
      name: receptionist.hire.names[1],
      job: receptionist.job,
      rules: { ask_first: true },
      trigger: { kind: 'app_event', app: 'WhatsApp' },
    });
    expect(typeof payload.idempotency_key).toBe('string');
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'w1' });
    // Their page opens on their first day.
    expect(useHomeStore.getState().firstDays.w1).toEqual({ started: true, nodeCount: 12 });
    // The composer held this job, which is now on the team.
    expect(useDraftStore.getState()).toMatchObject({ hiring: false, input: '', hireKey: null });
  });

  it('stops before hiring when a skill is not there', async () => {
    builtIns = [];
    const { result } = setup();
    expect(await hire(result)).toBe(false);
    expect(sendRequest).not.toHaveBeenCalledWith('hire_employee', expect.anything(), expect.anything());
    expect(pillToast).toHaveBeenCalledWith("Receptionist needs a skill that isn't available", { tone: 'error' });
    expect(useDraftStore.getState().hiring).toBe(false);
  });

  it('says why a hire failed and keeps its key for the retry', async () => {
    hireResponse = { success: false, error: 'save_failed' };
    const { result } = setup();
    expect(await hire(result)).toBe(false);
    expect(pillToast).toHaveBeenCalledWith("Couldn't save them. Press Hire again.", { tone: 'error' });
    expect(useDraftStore.getState().hiring).toBe(false);
    expect(useDraftStore.getState().hireKey).not.toBeNull();
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
  });
});
