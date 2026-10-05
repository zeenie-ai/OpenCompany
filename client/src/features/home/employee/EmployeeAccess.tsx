import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { ActionButton } from '@/components/ui/action-button';
import { pillToast } from '../ui/pillToast';

interface Access { id: string; app: string; action?: string; approved: boolean; revoked: boolean; parent_grant_id?: string | null; scope_apps?: string[]; account_id?: string }

export function EmployeeAccess({ workflowId, name, pendingOnly = false }: { workflowId: string; name: string; pendingOnly?: boolean }) {
  const { sendRequest } = useWebSocketActions();
  const cache = useQueryClient();
  const key = ['employee-access', workflowId];
  const access = useQuery({ queryKey: key, queryFn: async () => {
    const response = await sendRequest<{ success: boolean; access?: Access[]; error?: string }>('list_employee_access', { workflow_id: workflowId });
    if (!response?.success) throw new Error('Access could not be loaded.');
    return response.access || [];
  }, refetchInterval: 5000 });
  const decide = useMutation({ mutationFn: async ({ id, allow }: { id: string; allow: boolean }) => {
    const response = await sendRequest<{ success: boolean }>('decide_employee_access', { request_id: id, allow });
    if (!response?.success) throw new Error('That permission could not be changed.');
  }, onSuccess: () => cache.invalidateQueries({ queryKey: key }), onError: (error: Error) => pillToast(error.message, { tone: 'error' }) });
  const rows = (access.data || []).filter((row) => !row.parent_grant_id && (!pendingOnly || (!row.approved && !row.revoked)));
  if (pendingOnly && !rows.length) return null;
  return <div className="flex flex-col gap-3 rounded-card border border-border-default bg-bg-panel p-4">
    {!pendingOnly && <p className="m-0 font-medium">{name}’s app access</p>}
    {access.isError && !pendingOnly && <p role="alert">Access could not be loaded. Try again.</p>}
    {!access.isLoading && !rows.length && !pendingOnly && <p className="m-0 text-sm text-fg-muted">No app permissions to review.</p>}
    {rows.map((row) => <div key={row.id} className="flex flex-wrap items-center gap-3">
      <div className="min-w-0 flex-1">
        <p className="m-0 text-sm">{row.revoked ? `${name}’s team no longer has access to ${row.app}.` : row.approved ? `${name}’s team can use ${row.app} to ${row.action || 'help with their assigned work'}.` : `Allow ${name}’s team to use ${row.app} to ${row.action || 'help with their assigned work'}?`}</p>
        {row.scope_apps?.length ? <p className="mb-0 mt-1 text-sm text-fg-muted">Includes {row.scope_apps.join(', ')} for {row.action || 'their assigned work'}.</p> : null}
        {row.account_id && <p className="mb-0 mt-1 text-sm text-fg-muted">Account: {row.account_id}</p>}
      </div>
      {(!row.approved || row.revoked) && <ActionButton intent="config" disabled={decide.isPending} onClick={() => decide.mutate({ id: row.id, allow: true })}>{row.revoked ? 'Allow again' : 'Allow'}</ActionButton>}
      {!row.revoked && <ActionButton intent="config" disabled={decide.isPending} onClick={() => decide.mutate({ id: row.id, allow: false })}>{row.approved ? 'Remove access' : 'Not now'}</ActionButton>}
    </div>)}
  </div>;
}
