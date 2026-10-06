/**
 * The team, as TanStack queries: the list (`list_employees`) and one
 * employee's detail (`get_employee`). Both go through the stable
 * `useWebSocketActions()`, so Home never re-renders on a console line.
 *
 * `useEmployeeLifecycle()` keeps the cache current from broadcasts, and is
 * mounted once by the Home shell:
 * - `employee_lifecycle` hired / updated refresh the server queries. Event
 *   summaries may already be out of date, so they never replace cached rows.
 *   Confirmed removals prune the cache and refresh the remaining team.
 * - `workflow_lifecycle` created / renamed / imported mean a workflow
 *   (every workflow is an employee) appeared or changed name: the list is
 *   refetched, debounced so a burst costs one request.
 * CloudEvents duplicates are dropped by (source, id).
 */

import { useEffect } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { makeDebouncedInvalidator } from '@/lib/debouncedInvalidate';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { parseEmployeeDetail, parseEmployees, type EmployeeDetail, type EmployeeSummary } from './schemas';
import { EMPLOYEES_QUERY_KEY, employeeDetailKey, removeEmployee } from './employeeCache';

export { EMPLOYEES_QUERY_KEY, employeeDetailKey, removeEmployee } from './employeeCache';

const LIST_INVALIDATE_DEBOUNCE_MS = 300;
export const invalidateEmployees = makeDebouncedInvalidator(EMPLOYEES_QUERY_KEY, LIST_INVALIDATE_DEBOUNCE_MS, true);


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
    // Home's lifecycle listener is absent in Dev; re-entry must reconcile
    // even a warm cache whose removal broadcasts arrived while unmounted.
    refetchOnMount: 'always',
  });
}

export function useEmployeeDetailQuery(workflowId: string | null, options: { live?: boolean } = {}) {
  const { sendRequest, isReady } = useWebSocketActions();
  const cache = useQueryClient();
  const live = options.live === true;
  useEffect(() => {
    if (!live || !isReady || !workflowId) return;
    // A burst of tool/phase broadcasts costs at most one detail read every
    // three seconds. Polling below also catches durable task transitions and
    // deliveries that do not emit a node-status event.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const off = useNodeStatusStore.subscribe((state, previous) => {
      if (state.allStatuses[workflowId] === previous.allStatuses[workflowId] || timer !== undefined) return;
      timer = setTimeout(() => {
        timer = undefined;
        void cache.invalidateQueries({ queryKey: employeeDetailKey(workflowId), exact: true });
      }, 3000);
    });
    return () => {
      off();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [cache, isReady, live, workflowId]);
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
    // Live observers must reconcile immediately when the socket becomes
    // ready again, even when the retained snapshot is only seconds old.
    staleTime: live ? 0 : 30_000,
    refetchOnMount: 'always',
    refetchInterval: !live || !isReady ? false : (query) => {
      if (query.state.data === null) return false;
      const progress = query.state.data?.work_progress;
      return progress && !['idle', 'done', 'failed', 'cancelled'].includes(progress.state) ? 5000 : 15000;
    },
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

/** Read the current database state after a mutation or lifecycle signal. */
export function refreshEmployee(queryClient: QueryClient, workflowId: string): void {
  void queryClient.invalidateQueries({ queryKey: employeeDetailKey(workflowId), exact: true });
  invalidateEmployees(queryClient);
}

/** Apply one `employee_lifecycle` envelope to the cache. Exported for tests. */
export function applyEmployeeLifecycle(queryClient: QueryClient, event: LifecycleEnvelope): void {
  if (!event || event.specversion !== '1.0' || !firstSighting(event)) return;
  const type = event.type ?? '';
  const workflowId = event.data?.workflow_id ?? event.subject ?? '';
  if (!workflowId) return;
  if (type.endsWith('.removed')) {
    removeEmployee(queryClient, workflowId);
    return;
  }
  if (type.endsWith('.hired') || type.endsWith('.updated')) refreshEmployee(queryClient, workflowId);
}

/** Apply one `workflow_lifecycle` envelope to the team list. Exported for tests. */
export function applyWorkflowLifecycle(queryClient: QueryClient, event: LifecycleEnvelope): void {
  const type = event?.type ?? '';
  const workflowId = event?.subject ?? '';
  if (type.endsWith('.deleted') && workflowId) {
    removeEmployee(queryClient, workflowId);
  } else if (['.created', '.renamed', '.imported'].some((stage) => type.endsWith(stage))) {
    if (workflowId) refreshEmployee(queryClient, workflowId);
    else invalidateEmployees(queryClient);
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
