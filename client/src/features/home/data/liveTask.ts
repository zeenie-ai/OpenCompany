/**
 * The live "NOW" line: what a working employee is doing this moment, laid
 * over the server's task text. Reads the same live state the editor
 * canvas does (node statuses, the writeTodos cache), so it needs no extra
 * requests:
 *
 * 1. an in-progress todo of the employee's todo list ("Checking tomorrow's
 *    appointments"),
 * 2. otherwise, one of its watched nodes running ("Working on it..."),
 * 3. otherwise nothing, and the card shows the server's text ("Waiting for
 *    new WhatsApp messages").
 */

import { useQuery } from '@tanstack/react-query';
import { useCallback } from 'react';
import { todoQueryKey } from '@/lib/todoQuery';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import type { EmployeeSummary } from './schemas';

const TODO_ID_MARK = ':writeTodos:';

interface TodoItem {
  content?: unknown;
  status?: unknown;
}

export function currentTodo(todos: unknown): string | null {
  if (!Array.isArray(todos)) return null;
  const active = (todos as TodoItem[]).find((todo) => todo?.status === 'in_progress');
  const text = typeof active?.content === 'string' ? active.content.trim() : '';
  return text || null;
}

export function useLiveTask(employee: EmployeeSummary): { label: 'Now'; text: string } | null {
  const todoNodeId = employee.watch_node_ids.find((id) => id.includes(TODO_ID_MARK)) ?? null;
  const { data: todos } = useQuery<unknown>({
    queryKey: todoQueryKey(employee.workflow_id, todoNodeId ?? ''),
    queryFn: () => [],
    enabled: false,
    staleTime: Infinity,
  });
  const watchKey = employee.watch_node_ids.join('|');
  const running = useNodeStatusStore(
    useCallback(
      (state) => {
        const statuses = state.allStatuses[employee.workflow_id];
        if (!statuses) return false;
        return watchKey.split('|').some((id) => id && statuses[id]?.status === 'executing');
      },
      [employee.workflow_id, watchKey],
    ),
  );
  // While the agent waits for the owner in the browser its node is still
  // executing; the server's "Needs you…" task is the one to show.
  if (employee.status !== 'working' || employee.browser_request) return null;
  const todo = todoNodeId ? currentTodo(todos) : null;
  if (todo) return { label: 'Now', text: todo };
  if (running) return { label: 'Now', text: 'Working on it…' };
  return null;
}
