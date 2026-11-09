/**
 * The editor's Chat pane in the console panel: the shared chat
 * (features/chat), compact, over the open workflow's chat session
 * (`default` with no workflow open), showing the live generation only.
 * A message goes to the workflow's chat triggers; Clear clears the chat and
 * what the workflow's agents remember of it.
 *
 * A document an agent wrote opens from its card in the Canvas dock.
 *
 * The console's font size applies to the conversation. The `chat` panel
 * hook stays on ConsolePanel's root; the turns carry the `chat-msg-user`
 * / `chat-msg-bot` theme hooks.
 */

import type { Ref } from 'react';
import { toast } from 'sonner';
import { ActionButton } from '@/components/ui/action-button';
import { Badge } from '@/components/ui/badge';
import { ChatPane, useChatThread, useClearChat, type ChatPaneHandle, type NotifyTone } from '@/features/chat';
import { useAppStore } from '../../store/useAppStore';
import { useCanvasDockStore } from '../../stores/canvasDockStore';

const SEND_REFUSED: Record<string, { message: string; tone: NotifyTone }> = {
  // Nothing listens while the workflow is stopped, so the server saved nothing.
  not_running: { message: 'Start this workflow to chat with it.', tone: 'error' },
  // One answer at a time per conversation; the text is back in the box.
  run_in_progress: { message: 'The workflow is still answering your last message.', tone: 'info' },
};

function notify(message: string, tone: NotifyTone) {
  if (tone === 'error') toast.error(message);
  else if (tone === 'success') toast.success(message);
  else toast.info(message);
}

export function ConsoleChat({ fontSize, ref }: { fontSize: number; ref?: Ref<ChatPaneHandle> }) {
  const sessionId = useAppStore((s) => s.currentWorkflow?.id || 'default');
  const workflowName = useAppStore((s) => s.currentWorkflow?.name ?? '');
  const thread = useChatThread(sessionId, 'live');
  const clear = useClearChat(sessionId);
  const count = thread.data?.messages.length ?? 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-[32px] items-center justify-between border-b border-border-default bg-bg-elevated px-3 py-1.5">
        <span className="flex items-center gap-1.5 font-display text-sm font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
          Chat
          {count > 0 && (
            <Badge variant="success" className="text-xs">
              {count}
            </Badge>
          )}
        </span>
        {count > 0 && (
          <ActionButton
            intent="stop"
            disabled={clear.isPending}
            onClick={() => clear.mutate(undefined, { onError: () => notify('The chat could not be cleared. Try again.', 'error') })}
            title="Clear the chat, and what the agent remembers of it"
            className="h-6 px-2 text-xs"
          >
            Clear
          </ActionButton>
        )}
      </div>
      <div style={{ fontSize }} className="flex min-h-0 flex-1 flex-col">
        <ChatPane
          key={sessionId}
          ref={ref}
          host={{
            kind: 'dev',
            sessionId,
            scope: 'live',
            persona: { name: workflowName || 'Workflow', colorRole: 'agent' },
            composer: 'send',
            emptyState: (
              <p className="m-0 py-6 text-center text-xs text-muted-foreground">Send a message to trigger chatTrigger nodes</p>
            ),
            notify,
            // A document the workflow's agent wrote opens in the Canvas dock.
            openArtifact: (artifact) => {
              if (artifact.workflowId !== useAppStore.getState().currentWorkflow?.id) return;
              useCanvasDockStore.getState().focusItem(artifact.canvasNodeId, artifact.itemId, artifact.version);
            },
            onSendRefused: (code) => {
              const refused = SEND_REFUSED[code] ?? { message: 'Your message didn’t send. Try again.', tone: 'error' as const };
              notify(refused.message, refused.tone);
            },
            compact: true,
          }}
        />
      </div>
    </div>
  );
}

export default ConsoleChat;
