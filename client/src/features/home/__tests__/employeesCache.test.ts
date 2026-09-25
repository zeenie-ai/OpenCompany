/**
 * Keeping the team current from broadcasts: duplicates are dropped by
 * (source, id), a late broadcast never rolls a row back, removals delete,
 * and workflow changes refetch the list.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
import {
  EMPLOYEES_QUERY_KEY,
  applyEmployeeLifecycle,
  applyWorkflowLifecycle,
  employeeDetailKey,
  resetSeenEmployeeEvents,
} from '../data/employees';
import type { EmployeeSummary } from '../data/schemas';

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
  client = new QueryClient();
  client.setQueryData(EMPLOYEES_QUERY_KEY, [summary('a', 5), summary('b', 5)].map((raw) => ({ ...raw, apps: [], missing_apps: [] })));
});

afterEach(() => {
  vi.useRealTimers();
  client.clear();
});

describe('applyEmployeeLifecycle', () => {
  it('puts a hire first and updates in place', () => {
    applyEmployeeLifecycle(client, event('hired', summary('c', 1)));
    applyEmployeeLifecycle(client, event('updated', summary('b', 6)));
    expect(names()).toEqual(['c@1', 'a@5', 'b@6']);
  });

  it('keeps the newer row when an older broadcast arrives late', () => {
    applyEmployeeLifecycle(client, event('updated', summary('a', 9)));
    applyEmployeeLifecycle(client, event('updated', summary('a', 7)));
    expect(names()).toEqual(['a@9', 'b@5']);
  });

  it('drops a duplicate delivery of the same event', () => {
    const once = event('hired', summary('d', 1), 'same-id');
    applyEmployeeLifecycle(client, once);
    applyEmployeeLifecycle(client, { ...once });
    expect(names()?.filter((n) => n.startsWith('d'))).toHaveLength(1);
  });

  it('removes a fired employee and their detail', () => {
    client.setQueryData(employeeDetailKey('a'), { workflow_id: 'a' });
    applyEmployeeLifecycle(client, event('removed', summary('a', 10)));
    expect(names()).toEqual(['b@5']);
    expect(client.getQueryData(employeeDetailKey('a'))).toBeUndefined();
  });

  it('refetches when the broadcast carries no usable summary, and ignores non-CloudEvents', () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    applyEmployeeLifecycle(client, { ...event('updated', summary('a', 11)), data: { workflow_id: 'a' } });
    vi.advanceTimersByTime(400);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: EMPLOYEES_QUERY_KEY });
    applyEmployeeLifecycle(client, { ...event('updated', summary('a', 12)), specversion: '0.3' });
    expect(names()).toEqual(['a@5', 'b@5']);
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
    expect(invalidate).toHaveBeenCalledTimes(1);
  });
});
