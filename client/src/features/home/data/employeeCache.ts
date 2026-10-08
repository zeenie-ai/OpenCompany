import type { QueryClient } from '@tanstack/react-query';
import { useHomeStore } from '../state/homeStore';
import type { EmployeeSummary } from './schemas';

export const EMPLOYEES_QUERY_KEY = ['employees'] as const;
export const employeeDetailKey = (workflowId: string) => ['employees', 'detail', workflowId] as const;

/** Reconcile a confirmed deletion in either mode, including an in-flight list. */
export function removeEmployee(queryClient: QueryClient, workflowId: string): void {
  // Cancellation reverts query state synchronously. Prune afterwards so an
  // older request cannot restore the employee when its response arrives.
  void queryClient.cancelQueries({ queryKey: EMPLOYEES_QUERY_KEY, exact: true });
  void queryClient.cancelQueries({ queryKey: employeeDetailKey(workflowId), exact: true });
  queryClient.setQueryData<EmployeeSummary[]>(EMPLOYEES_QUERY_KEY, (list) =>
    list?.filter((employee) => employee.workflow_id !== workflowId),
  );
  queryClient.removeQueries({ queryKey: employeeDetailKey(workflowId), exact: true });

  useHomeStore.setState((state) => ({
    view: state.view.kind === 'employee' && state.view.workflowId === workflowId
      ? { kind: 'hire' }
      : state.view,
    workspaceFor: state.workspaceFor === workflowId ? null : state.workspaceFor,
    hireNotice: state.hireNotice?.workflowId === workflowId ? null : state.hireNotice,
    glow: state.glow?.workflowId === workflowId ? null : state.glow,
  }));
  useHomeStore.getState().endFirstDay(workflowId);

  // Refresh anything else the canceled list request would have brought in.
  void queryClient.invalidateQueries({ queryKey: EMPLOYEES_QUERY_KEY, exact: true });
}
