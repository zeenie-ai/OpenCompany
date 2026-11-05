/**
 * The owner's message: a bubble on the right (design handoff chat,
 * "Thread"). A button they pressed in an interface the employee showed
 * (`kind: "action"`) reads as the press: the button's label beside a
 * pointer, in a quieter pill. `chat-msg chat-msg-user` is the theme hook
 * Atomic paints.
 *
 * Files sent with it show above it (images as thumbnails, other files as
 * chips); a message that is only files has no bubble.
 *
 * Under it, the hover bar: when they sent it, ‹ 1 / 2 › between their edits
 * of it, Edit (it opens in place; sending the edit starts a new branch of
 * the conversation), and Copy. Edit shows only where the server allows it
 * (`editable`) and the chat offers it (TurnActions).
 */

import { Check, Copy, MousePointerClick, Pencil } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { ChatMessage } from '../data/schemas';
import { timeLabel } from '../thread/timeLabel';
import { useTurnActions } from '../thread/turnActions';
import { TurnMeta } from './TurnMeta';
import { useCopied } from './useCopied';
import { MessageAttachments } from './MessageAttachments';
import { UserEditBox } from './UserEditBox';
import { VersionStepper } from './VersionStepper';

export function UserTurn({ message, now, latest, compact }: { message: ChatMessage; now: Date; latest: boolean; compact: boolean }) {
  const actions = useTurnActions();
  const [copied, copy] = useCopied();
  const pressed = message.kind === 'action';
  const editing = Boolean(actions && actions.editingId === message.id);
  const canEdit = Boolean(actions && message.editable && !pressed && !message.pending);
  const hasText = message.text.trim().length > 0;

  return (
    <div data-turn="user" data-message={message.id} className="chat-turn-user group/turn flex flex-col items-end gap-1.5">
      {message.attachments.length > 0 && <MessageAttachments attachments={message.attachments} />}
      {editing && actions ? (
        <UserEditBox
          initial={message.text}
          compact={compact}
          busy={actions.busy}
          onCancel={actions.cancelEdit}
          onSave={(text) => actions.saveEdit(message.id, text)}
        />
      ) : pressed ? (
        <p
          className={cn(
            'm-0 flex max-w-[85%] items-center gap-2 rounded-pill border border-border-default bg-bg-panel text-fg-muted wrap-anywhere',
            compact ? 'px-3 py-1.5 text-sm' : 'px-3.5 py-2 text-sm',
          )}
        >
          <MousePointerClick aria-hidden className="size-3.5 shrink-0 text-fg-faint" strokeWidth={2} />
          <span className="sr-only">Pressed </span>
          <span className="font-medium text-fg-default">{message.text}</span>
        </p>
      ) : !hasText ? null : (
        <p
          className={cn(
            'chat-msg chat-msg-user m-0 max-w-[85%] rounded-draft rounded-br-sm border border-border-default bg-bg-elevated whitespace-pre-wrap text-fg-default wrap-anywhere',
            compact ? 'px-3 py-2 leading-normal' : 'px-4 py-2.5 text-md leading-message',
          )}
        >
          {message.text}
        </p>
      )}
      {!editing &&
        (message.pending ? (
          <TurnMeta shown>Sending…</TurnMeta>
        ) : (
          <TurnMeta shown={latest} className="h-7 gap-0.5 px-0">
            <span className="px-1.5">{timeLabel(message.timestamp, now)}</span>
            {message.siblings && actions && (
              <VersionStepper siblings={message.siblings} noun="version" disabled={actions.busy} onSwitch={actions.switchTo} />
            )}
            {canEdit && actions && (
              <Button
                variant="quiet"
                size="icon-sm"
                disabled={actions.busy}
                onClick={() => actions.startEdit(message.id)}
                aria-label="Edit message"
                title="Edit"
              >
                <Pencil aria-hidden className="size-3.5" strokeWidth={1.9} />
              </Button>
            )}
            {!pressed && hasText && (
              <Button
                variant="quiet"
                size="icon-sm"
                onClick={() => copy(message.text)}
                aria-label={copied ? 'Copied' : 'Copy message'}
                title={copied ? 'Copied' : 'Copy'}
              >
                {copied ? (
                  <Check aria-hidden className="size-3.5 text-action-run-ink" strokeWidth={1.9} />
                ) : (
                  <Copy aria-hidden className="size-3.5" strokeWidth={1.9} />
                )}
              </Button>
            )}
          </TurnMeta>
        ))}
    </div>
  );
}
