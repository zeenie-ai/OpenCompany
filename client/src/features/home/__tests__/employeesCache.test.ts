/**
 * Lifecycle events refresh authoritative server queries; their payloads
 * never overwrite rows. Confirmed deletion prunes caches and navigation.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryObserver } from '@tanstack/react-query';
import {
  EMPLOYEES_QUERY_KEY,
  applyEmployeeLifecycle,
  applyWorkflowLifecycle,
  employeeDetailKey,
  removeEmployee,
  resetSeenEmployeeEvents,
} from '../data/employees';
import type { EmployeeSummary } from '../data/schemas';
import { useHomeStore } from '../state/homeStore';

function summary(id: string, revision: number, name = id): Record<string, unknown> {
  return { workflow_id: id, name, revision, control: {} };
}

let sequence = 0;
function event(stage: 'hired' | 'updated' | 'removed', employee: Record<string, unknown>, id = `e${++sequence}`) {
  return {
    specversion: '1.0',
    id,
    source: 'opencompany://services/employees',
    type: `com.opencompany.employee.${stage}`,
    subject: employee.workflow_id as string,
    data: { workflow_id: employee.workflow_id as string, revision: employee.revision as number, employee: stage === 'removed' ? undefined : employee },
  };
}

let client: QueryClient;
const names = () => client.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)?.map((e) => `${e.workflow_id}@${e.revision}`);

beforeEach(() => {
  vi.useFakeTimers();
  resetSeenEmployeeEvents();
  useHomeStore.setState({ view: { kind: 'hire' }, workspaceFor: null, hireNotice: null, glow: null });
  client = new QueryClient();
  client.setQueryData(EMPLOYEES_QUERY_KEY, [summary('a', 5), summary('b', 5)].map((raw) => ({ ...raw, apps: [], missing_apps: [] })));
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  client.clear();
  vi.restoreAllMocks();
});

describe('applyEmployeeLifecycle', () => {
  it('coalesces hire/update signals into a server list refresh without merging their summaries', async () => {
    const backendRows = [summary('c', 4), summary('a', 5), summary('b', 8)] as unknown as EmployeeSummary[];
    const fetch = vi.fn(async () => backendRows);
    const list = new QueryObserver(client, { queryKey: EMPLOYEES_QUERY_KEY, queryFn: fetch, staleTime: Infinity });
    const unsubscribe = list.subscribe(() => {});
    applyEmployeeLifecycle(client, event('hired', summary('c', 1)));
    applyEmployeeLifecycle(client, event('updated', summary('b', 6)));
    expect(names()).toEqual(['a@5', 'b@5']);
    expect(fetch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(names()).toEqual(['c@4', 'a@5', 'b@8']);
    unsubscribe();
  });

  it('invalidates the affected detail without replacing it with a late summary', () => {
    client.setQueryData(employeeDetailKey('a'), { workflow_id: 'a', revision: 12, job: 'Current database job' });
    client.setQueryData(employeeDetailKey('b'), { workflow_id: 'b', revision: 5 });
    applyEmployeeLifecycle(client, event('updated', summary('a', 9)));
    applyEmployeeLifecycle(client, event('updated', summary('a', 7)));
    expect(client.getQueryData(employeeDetailKey('a'))).toMatchObject({ revision: 12, job: 'Current database job' });
    expect(client.getQueryState(employeeDetailKey('a'))?.isInvalidated).toBe(true);
    expect(client.getQueryState(employeeDetailKey('b'))?.isInvalidated).toBe(false);
    vi.advanceTimersByTime(300);
    expect(client.getQueryState(employeeDetailKey('b'))?.isInvalidated).toBe(false);
  });

  it('drops a duplicate delivery of the same event', () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const once = event('hired', summary('d', 1), 'same-id');
    applyEmployeeLifecycle(client, once);
    applyEmployeeLifecycle(client, { ...once });
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: employeeDetailKey('d'), exact: true });
    vi.advanceTimersByTime(300);
    // Once each: the team list, this month's costs and the task counts.
    const later = invalidate.mock.calls.slice(1).map(([filters]) => filters?.queryKey);
    expect(later).toHaveLength(3);
    expect(later).toEqual(expect.arrayContaining([EMPLOYEES_QUERY_KEY, ['workflowCosts'], ['employeeUsage']]));
    expect(names()).toEqual(['a@5', 'b@5']);
  });

  it('reads this month’s usage again when an employee changes', () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    applyEmployeeLifecycle(client, event('updated', summary('a', 6)));
    vi.advanceTimersByTime(300);
    const keys = invalidate.mock.calls.map(([filters]) => filters?.queryKey);
    expect(keys).toContainEqual(['workflowCosts']);
    expect(keys).toContainEqual(['employeeUsage']);
  });

  it('removes a fired employee and their detail', () => {
    client.setQueryData(employeeDetailKey('a'), { workflow_id: 'a' });
    applyEmployeeLifecycle(client, event('removed', summary('a', 10)));
    expect(names()).toEqual(['b@5']);
    expect(client.getQueryData(employeeDetailKey('a'))).toBeUndefined();
  });

  it('reads the database after delayed summaries and never recreates a removed employee from their payloads', async () => {
    const fetch = vi.fn(async () => [summary('b', 5)] as unknown as EmployeeSummary[]);
    const list = new QueryObserver(client, { queryKey: EMPLOYEES_QUERY_KEY, queryFn: fetch, staleTime: Infinity });
    const unsubscribe = list.subscribe(() => {});
    applyEmployeeLifecycle(client, event('removed', summary('a', 10)));
    applyEmployeeLifecycle(client, event('updated', summary('a', 9)));
    applyEmployeeLifecycle(client, event('hired', summary('a', 1)));
    expect(names()).toEqual(['b@5']);
    await vi.advanceTimersByTimeAsync(300);
    expect(fetch).toHaveBeenCalled();
    expect(names()).toEqual(['b@5']);
    expect(client.getQueryData(employeeDetailKey('a'))).toBeUndefined();
    unsubscribe();
  });

  it('refetches when the broadcast carries no usable summary, and ignores non-CloudEvents', () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    applyEmployeeLifecycle(client, { ...event('updated', summary('a', 11)), data: { workflow_id: 'a' } });
    vi.advanceTimersByTime(400);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: EMPLOYEES_QUERY_KEY, exact: true });
    invalidate.mockClear();
    applyEmployeeLifecycle(client, { ...event('updated', summary('a', 12)), specversion: '0.3' });
    vi.advanceTimersByTime(300);
    expect(invalidate).not.toHaveBeenCalled();
    expect(names()).toEqual(['a@5', 'b@5']);
  });
});

describe('removeEmployee', () => {
  it('clears navigation and one-shot state belonging to the deleted employee', () => {
    useHomeStore.setState({
      view: { kind: 'employee', workflowId: 'a' },
      workspaceFor: 'a',
      hireNotice: { workflowId: 'a', name: 'Maya', warnings: ['Missing app'] },
      glow: { workflowId: 'a', nonce: 1 },
    });
    removeEmployee(client, 'a');
    expect(names()).toEqual(['b@5']);
    expect(useHomeStore.getState()).toMatchObject({ view: { kind: 'hire' }, workspaceFor: null, hireNotice: null, glow: null });
  });

  it('preserves another employee’s page, workspace and notices', () => {
    const state = {
      view: { kind: 'employee' as const, workflowId: 'b' },
      workspaceFor: 'b',
      hireNotice: { workflowId: 'b', name: 'Theo', warnings: ['Missing app'] },
      glow: { workflowId: 'b', nonce: 2 },
    };
    useHomeStore.setState(state);
    removeEmployee(client, 'a');
    expect(useHomeStore.getState()).toMatchObject(state);
  });

  it('cancels stale list and detail responses so they cannot restore a deleted employee', async () => {
    let resolveList!: (value: EmployeeSummary[]) => void;
    let resolveDetail!: (value: { workflow_id: string }) => void;
    let listSignal!: AbortSignal;
    let detailSignal!: AbortSignal;
    client.setQueryData(employeeDetailKey('a'), { workflow_id: 'a' });
    const staleList = client.getQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY)!;
    const listRequest = client.fetchQuery({
      queryKey: EMPLOYEES_QUERY_KEY,
      queryFn: ({ signal }) => {
        listSignal = signal;
        return new Promise<EmployeeSummary[]>((resolve) => { resolveList = resolve; });
      },
      staleTime: 0,
    }).catch(() => undefined);
    const detailRequest = client.fetchQuery({
      queryKey: employeeDetailKey('a'),
      queryFn: ({ signal }) => {
        detailSignal = signal;
        return new Promise<{ workflow_id: string }>((resolve) => { resolveDetail = resolve; });
      },
      staleTime: 0,
    }).catch(() => undefined);
    removeEmployee(client, 'a');
    expect(listSignal.aborted).toBe(true);
    expect(detailSignal.aborted).toBe(true);
    resolveList(staleList);
    resolveDetail({ workflow_id: 'a' });
    await Promise.all([listRequest, detailRequest]);
    expect(names()).toEqual(['b@5']);
    expect(client.getQueryData(employeeDetailKey('a'))).toBeUndefined();
    expect(client.getQueryState(EMPLOYEES_QUERY_KEY)?.isInvalidated).toBe(true);
  });
});

describe('applyWorkflowLifecycle', () => {
  it('removes a deleted workflow and refetches on created, renamed or imported', () => {
    applyWorkflowLifecycle(client, { type: 'com.opencompany.workflow.deleted', subject: 'b' });
    expect(names()).toEqual(['a@5']);
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    applyWorkflowLifecycle(client, { type: 'com.opencompany.workflow.created', subject: 'z' });
    applyWorkflowLifecycle(client, { type: 'com.opencompany.workflow.renamed', subject: 'a' });
    vi.advanceTimersByTime(400);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: employeeDetailKey('z'), exact: true });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: employeeDetailKey('a'), exact: true });
    expect(invalidate.mock.calls.filter(([filters]) => filters?.queryKey === EMPLOYEES_QUERY_KEY)).toHaveLength(1);
  });
});
