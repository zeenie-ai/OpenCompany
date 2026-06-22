/**
 * TodoEditor — editable Current Todos manager for the writeTodos node.
 *
 * Replaces the empty generic-params card ("Todos — No properties") in the
 * parameter panel's middle section. Sourced from the LIVE workflow todos in
 * the backend TodoService (keyed by the open workflow, node_id fallback) via
 * the `get_todos` / `set_todos` WS handlers — NOT from `parameters.todos`.
 *
 * Because TodoService keys by workflow, all writeTodos nodes in one workflow
 * share a single list (surfaced in the header). The list updates live when an
 * agent calls the write_todos tool mid-run (the `todos_updated` CloudEvent
 * lands in this query's cache via WebSocketContext).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Circle, CircleDot, Plus, Trash2 } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { ActionButton } from '@/components/ui/action-button';
import { cn } from '@/lib/utils';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useAppStore } from '../../store/useAppStore';

type TodoStatus = 'pending' | 'in_progress' | 'completed';

interface ServerTodo {
  content: string;
  status: TodoStatus;
}

interface TodoRow extends ServerTodo {
  /** Transient client id for stable React keys — not persisted. */
  id: string;
}

const STATUS_ORDER: TodoStatus[] = ['pending', 'in_progress', 'completed'];

const STATUS_META: Record<TodoStatus, { Icon: typeof Circle; cls: string; label: string }> = {
  pending: { Icon: Circle, cls: 'text-muted-foreground', label: 'Pending' },
  in_progress: { Icon: CircleDot, cls: 'text-warning', label: 'In progress' },
  completed: { Icon: CheckCircle2, cls: 'text-success', label: 'Completed' },
};

let _seq = 0;
const nextId = () => `todo-${++_seq}`;

const toRows = (todos: ServerTodo[]): TodoRow[] =>
  todos.map((t) => ({ id: nextId(), content: t.content ?? '', status: (t.status as TodoStatus) ?? 'pending' }));

const cycle = (s: TodoStatus): TodoStatus => STATUS_ORDER[(STATUS_ORDER.indexOf(s) + 1) % STATUS_ORDER.length];

interface Props {
  nodeId: string;
}

const TodoEditor: React.FC<Props> = ({ nodeId }) => {
  const { sendRequest, isReady } = useWebSocket();
  const currentWorkflowId = useAppStore((s) => s.currentWorkflow?.id);
  const queryClient = useQueryClient();

  // Session key mirrors the backend write op (`workflow_id or node_id`).
  const sessionKey = currentWorkflowId ?? nodeId;

  const { data: serverTodos } = useQuery<ServerTodo[]>({
    queryKey: ['todos', sessionKey],
    queryFn: async () => {
      const resp = await sendRequest<{ todos?: ServerTodo[] }>('get_todos', {
        workflow_id: currentWorkflowId,
        node_id: nodeId,
      });
      return resp?.todos ?? [];
    },
    enabled: isReady,
    staleTime: 0,
  });

  const [rows, setRows] = useState<TodoRow[]>([]);
  // While a row's content input is focused we must NOT let an incoming
  // server/broadcast sync overwrite it mid-type (same clobber lesson as the
  // credentials Default-Parameters fix).
  const editingRef = useRef<string | null>(null);

  // Sync local rows from the server query — only when not actively editing.
  useEffect(() => {
    if (editingRef.current !== null) return;
    setRows(toRows(serverTodos ?? []));
  }, [serverTodos]);

  // Persist the current rows (filtering empties — TodoService.write drops
  // empty-content items, so we match that and never send blank rows).
  const persist = useCallback(
    (next: TodoRow[]) => {
      setRows(next);
      const payload: ServerTodo[] = next
        .filter((r) => r.content.trim() !== '')
        .map((r) => ({ content: r.content.trim(), status: r.status }));
      // Optimistic cache update so other consumers (and a reopen) see it now.
      queryClient.setQueryData(['todos', sessionKey], payload);
      void sendRequest('set_todos', {
        workflow_id: currentWorkflowId,
        node_id: nodeId,
        todos: payload,
      }).catch(() => {
        // Non-fatal: a failed save leaves the optimistic UI; the next
        // get_todos / todos_updated reconciles. Surface nothing noisy here.
      });
    },
    [queryClient, sendRequest, currentWorkflowId, nodeId, sessionKey],
  );

  const addTodo = useCallback(() => {
    // New row is local-only until it has content (empty rows aren't persisted).
    const row: TodoRow = { id: nextId(), content: '', status: 'pending' };
    editingRef.current = row.id;
    setRows((prev) => [...prev, row]);
  }, []);

  const deleteTodo = useCallback(
    (id: string) => {
      persist(rows.filter((r) => r.id !== id));
    },
    [rows, persist],
  );

  const cycleStatus = useCallback(
    (id: string) => {
      persist(rows.map((r) => (r.id === id ? { ...r, status: cycle(r.status) } : r)));
    },
    [rows, persist],
  );

  const editContent = useCallback((id: string, content: string) => {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, content } : r)));
  }, []);

  const commitContent = useCallback(
    (id: string) => {
      editingRef.current = null;
      // Drop a still-empty new row on blur; otherwise persist the edit.
      const next = rows.filter((r) => r.id !== id || r.content.trim() !== '');
      persist(next);
    },
    [rows, persist],
  );

  const counts = rows.reduce(
    (acc, r) => {
      acc[r.status] += 1;
      return acc;
    },
    { pending: 0, in_progress: 0, completed: 0 } as Record<TodoStatus, number>,
  );

  return (
    <div className="flex flex-1 flex-col overflow-hidden p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-fg-default shrink-0">
          Current Todos
        </span>
        <Badge variant="secondary" className="min-w-0 truncate normal-case tracking-normal">
          {counts.completed} done · {counts.in_progress} active · {counts.pending} pending
        </Badge>
        <ActionButton intent="save" onClick={addTodo} className="ml-auto h-8 shrink-0">
          <Plus className="h-3.5 w-3.5" />
          Add
        </ActionButton>
      </div>

      <p className="mb-3 text-xs text-muted-foreground">
        Live todo list for this workflow (shared by every Write Todos node in the workflow).
      </p>

      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
        {rows.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 py-10 text-center">
            <CircleDot className="h-6 w-6 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">No todos yet. Add one to start planning.</p>
            <ActionButton intent="save" onClick={addTodo} className="h-8">
              <Plus className="h-3.5 w-3.5" />
              Add todo
            </ActionButton>
          </div>
        ) : (
          rows.map((row) => {
            const meta = STATUS_META[row.status];
            const { Icon } = meta;
            return (
              <div key={row.id} className="flex items-center gap-2 rounded-md border border-border bg-muted/40 p-2">
                <button
                  type="button"
                  onClick={() => cycleStatus(row.id)}
                  title={`${meta.label} — click to cycle`}
                  className={cn('inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md hover:bg-accent', meta.cls)}
                >
                  <Icon className="h-4 w-4" />
                </button>
                <Input
                  value={row.content}
                  placeholder="Describe a task…"
                  onChange={(e) => editContent(row.id, e.target.value)}
                  onFocus={() => {
                    editingRef.current = row.id;
                  }}
                  onBlur={() => commitContent(row.id)}
                  className={cn('h-8 min-w-0 flex-1', row.status === 'completed' && 'line-through text-muted-foreground')}
                />
                <ActionButton intent="stop" onClick={() => deleteTodo(row.id)} className="h-8 w-8 shrink-0 justify-center p-0" aria-label="Delete todo">
                  <Trash2 className="h-3.5 w-3.5" />
                </ActionButton>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default TodoEditor;
