/**
 * The team, as TanStack queries: the list (`list_employees`) and one
 * employee's detail (`get_employee`). Both go through the stable
 * `useWebSocketActions()`, so Home never re-renders on a console line.
 *
 * `useEmployeeLifecycle()` keeps the cache current from broadcasts, and is
 * mounted once by the Home shell:
 * - `employee_lifecycle` hired / updated carry the fresh summary: upserted
 *   when newer than what the cache holds (by `revision`), so a delayed
 *   broadcast never rolls a row back; removed deletes it.
 * - `workflow_lifecycle` created / renamed / imported mean a workflow
 *   (every workflow is an employee) appeared or changed name: the list is
 *   refetched, debounced so a burst costs one request.
 * CloudEvents duplicates are dropped by (source, id).
 */

import { useEffect } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { makeDebouncedInvalidator } from '@/lib/debouncedInvalidate';
import { parseEmployee, parseEmployeeDetail, parseEmployees, type EmployeeDetail, type EmployeeSummary } from './schemas';

export const EMPLOYEES_QUERY_KEY = ['employees'] as const;
export const employeeDetailKey = (workflowId: string) => ['employees', 'detail', workflowId] as const;

const LIST_INVALIDATE_DEBOUNCE_MS = 300;
export const invalidateEmployees = makeDebouncedInvalidator(EMPLOYEES_QUERY_KEY, LIST_INVALIDATE_DEBOUNCE_MS);


/** Dev builds only: `?fixture=employees` shows the handoff's sample team. */
function fixtureTeamRequested(): boolean {
  return import.meta.env.DEV && typeof window !== 'undefined' && /[?&]fixture=employees\b/.test(window.location.search);
}

export function useEmployeesQuery() {
  const { sendRequest, isReady } = useWebSocketActions();
  const fixture = fixtureTeamRequested();
  return useQuery<EmployeeSummary[], Error>({
    queryKey: EMPLOYEES_QUERY_KEY,
    queryFn: async () => {
      if (import.meta.env.DEV && fixture) return (await import('./fixtures')).fixtureEmployees();
      const response = await sendRequest<{ success?: boolean; employees?: unknown; error?: string }>('list_employees', {});
      if (response?.success === false) throw new Error(response.error || 'Could not load your team');
      return parseEmployees(response?.employees);
    },
    enabled: isReady || fixture,
    staleTime: 30_000,
  });
}

export function useEmployeeDetailQuery(workflowId: string | null) {
  const { sendRequest, isReady } = useWebSocketActions();
  return useQuery<EmployeeDetail | null, Error>({
    queryKey: employeeDetailKey(workflowId ?? ''),
    queryFn: async () => {
      const response = await sendRequest<{ success?: boolean; employee?: unknown; error?: string }>('get_employee', {
        workflow_id: workflowId,
      });
      if (response?.success === false) {
        if (response.error === 'not_found') return null;
        throw new Error(response.error || 'Could not load this employee');
      }
      return parseEmployeeDetail(response?.employee);
    },
    enabled: isReady && Boolean(workflowId),
    staleTime: 30_000,
  });
}

// ----- broadcasts -----

interface LifecycleEnvelope {
  specversion?: string;
  id?: string;
  source?: string;
  type?: string;
  subject?: string | null;
  data?: { workflow_id?: string; revision?: number; employee?: unknown } | null;
}

const SEEN_LIMIT = 256;
const seen = new Set<string>();

/** True the first time a (source, id) pair is seen. Bounded. */
function firstSighting(event: LifecycleEnvelope): boolean {
  if (!event.id || !event.source) return true;
  const key = `${event.source} ${event.id}`;
  if (seen.has(key)) return false;
  seen.add(key);
  if (seen.size > SEEN_LIMIT) {
    const oldest = seen.values().next().value;
    if (oldest !== undefined) seen.delete(oldest);
  }
  return true;
}

/** Test-only: forget the (source, id) pairs seen so far. */
export function resetSeenEmployeeEvents(): void {
  seen.clear();
}

/** Put a summary into the cache unless the cache already holds a newer one.
 *  New employees go first (placeFirst), as a fresh hire does. */
export function upsertEmployee(queryClient: QueryClient, summary: EmployeeSummary, placeFirst: boolean): void {
  queryClient.setQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY, (list) => {
    if (!list) return list;
    const index = list.findIndex((item) => item.workflow_id === summary.workflow_id);
    if (index === -1) return placeFirst ? [summary, ...list] : [...list, summary];
    if (list[index].revision > summary.revision) return list;
    const next = list.slice();
    next[index] = summary;
    return next;
  });
  queryClient.setQueryData<EmployeeDetail | null>(employeeDetailKey(summary.workflow_id), (detail) =>
    detail && detail.revision <= summary.revision ? { ...detail, ...summary } : detail,
  );
}

function remove(queryClient: QueryClient, workflowId: string): void {
  queryClient.setQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY, (list) =>
    list ? list.filter((item) => item.workflow_id !== workflowId) : list,
  );
  queryClient.removeQueries({ queryKey: employeeDetailKey(workflowId) });
}

/** Apply one `employee_lifecycle` envelope to the cache. Exported for tests. */
export function applyEmployeeLifecycle(queryClient: QueryClient, event: LifecycleEnvelope): void {
  if (!event || event.specversion !== '1.0' || !firstSighting(event)) return;
  const type = event.type ?? '';
  const workflowId = event.data?.workflow_id ?? event.subject ?? '';
  if (!workflowId) return;
  if (type.endsWith('.removed')) {
    remove(queryClient, workflowId);
    return;
  }
  const summary = parseEmployee(event.data?.employee);
  if (!summary || summary.workflow_id !== workflowId) {
    invalidateEmployees(queryClient);
    return;
  }
  upsertEmployee(queryClient, summary, type.endsWith('.hired'));
}

/** Apply one `workflow_lifecycle` envelope to the team list. Exported for tests. */
export function applyWorkflowLifecycle(queryClient: QueryClient, event: LifecycleEnvelope): void {
  const type = event?.type ?? '';
  const workflowId = event?.subject ?? '';
  if (type.endsWith('.deleted') && workflowId) {
    remove(queryClient, workflowId);
  } else if (['.created', '.renamed', '.imported'].some((stage) => type.endsWith(stage))) {
    invalidateEmployees(queryClient);
  }
}

export function useEmployeeLifecycle(): void {
  const queryClient = useQueryClient();
  const { addEventListener } = useWebSocketActions();
  useEffect(() => {
    const offEmployees = addEventListener('employee_lifecycle', (data) => applyEmployeeLifecycle(queryClient, data));
    const offWorkflows = addEventListener('workflow_lifecycle', (data) => applyWorkflowLifecycle(queryClient, data));
    return () => {
      offEmployees();
      offWorkflows();
    };
  }, [addEventListener, queryClient]);
}
