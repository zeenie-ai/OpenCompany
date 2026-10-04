/**
 * Who an employee is: their name and their photo (server
 * services/employees/handlers.py `rename_employee`, `set_employee_photo`).
 * Both refresh the team afterwards; the server tells every other client.
 *
 * A photo is uploaded into the employee's workspace (`uploads/`, through the
 * same route the chat's attachments use) and then given to them by path;
 * the server checks it is an image it can show.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useWebSocketActions } from '@/contexts/WebSocketContext';
import { uploadToWorkspace } from '@/lib/workspaceUpload';
import { refreshEmployee } from './employees';

/** The images a photo may be; the server refuses any other. */
export const PHOTO_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'] as const;
/** Mirrors `EMPLOYEE_PHOTO_MAX_BYTES` in server/services/media/limits.py. The
 *  server decides; checking here only gives a better message sooner. */
export const PHOTO_MAX_BYTES = 5 * 1024 * 1024;

type Reply = { success?: boolean; error?: string; detail?: string };

/** Rename an employee. Errors carry the server's code. */
export function useRenameEmployee() {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation<void, Error, { workflowId: string; name: string }>({
    mutationFn: async ({ workflowId, name }) => {
      const reply = await sendRequest<Reply>('rename_employee', { workflow_id: workflowId, name });
      if (reply?.success === false) throw new Error(reply.error || 'failed');
    },
    onSettled: (_data, _error, { workflowId }) => refreshEmployee(queryClient, workflowId),
  });
}

/** Give an employee a photo, or take it away (`file: null`). Errors carry a
 *  message the owner can act on. */
export function useSetEmployeePhoto() {
  const { sendRequest } = useWebSocketActions();
  const queryClient = useQueryClient();
  return useMutation<void, Error, { workflowId: string; file: File | null }>({
    mutationFn: async ({ workflowId, file }) => {
      let path: string | null = null;
      if (file) {
        if (!(PHOTO_TYPES as readonly string[]).includes(file.type)) throw new Error('A photo is a PNG, JPEG, WebP or GIF image.');
        if (file.size > PHOTO_MAX_BYTES) throw new Error(`A photo is at most ${PHOTO_MAX_BYTES / (1024 * 1024)} MB.`);
        path = (await uploadToWorkspace(file, workflowId)).path;
      }
      const reply = await sendRequest<Reply>('set_employee_photo', { workflow_id: workflowId, path });
      if (reply?.success === false) throw new Error(reply.detail || 'That photo couldn’t be used. Try another.');
    },
    onSettled: (_data, _error, { workflowId }) => refreshEmployee(queryClient, workflowId),
  });
}
