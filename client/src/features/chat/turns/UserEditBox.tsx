/**
 * Editing one of the owner's messages in place (design handoff chat, "Edit
 * & branch"): the text in a box where the bubble was, that editing starts a
 * new branch of the conversation, Cancel and Send. Enter sends (Shift+Enter
 * a new line), Esc cancels. Sending an unchanged or empty message does
 * nothing.
 */

import { useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { isSendKey } from '@/lib/composerKeys';
import { useAutoGrow } from '@/lib/useAutoGrow';
import { cn } from '@/lib/utils';

export function UserEditBox({
  initial,
  compact,
  busy,
  onCancel,
  onSave,
}: {
  initial: string;
  compact: boolean;
  /** The conversation cannot change now: Send waits. */
  busy: boolean;
  onCancel: () => void;
  onSave: (text: string) => void;
}) {
  const [text, setText] = useState(initial);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(boxRef, text);
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  }, []);
  const changed = text.trim().length > 0 && text.trim() !== initial.trim();
  const save = () => {
    if (changed && !busy) onSave(text.trim());
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onCancel();
      return;
    }
    if (!isSendKey(event)) return;
    event.preventDefault();
    save();
  };
  return (
    <div
      className={cn(
        'flex w-full flex-col gap-2.5 rounded-draft border border-border-strong bg-bg-panel',
        compact ? 'px-3 py-2' : 'px-3.5 py-3',
      )}
    >
      <Textarea
        ref={boxRef}
        variant="bare"
        rows={2}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        aria-label="Edit your message"
        className={cn(
          'max-h-(--h-chat-composer-max) overflow-hidden text-fg-default field-sizing-fixed',
          compact ? 'text-sm leading-normal' : 'text-md leading-message',
        )}
      />
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-fg-faint">Editing starts a new branch of the conversation</span>
        <Button variant="quiet" size="sm" onClick={onCancel} className="ml-auto h-7.5 rounded-lg border-border-default px-3 text-xs font-semibold text-fg-default">
          Cancel
        </Button>
        <Button
          variant="invert"
          size="sm"
          onClick={save}
          disabled={!changed || busy}
          aria-label="Send edit"
          className="h-7.5 rounded-lg px-3 text-xs font-semibold"
        >
          Send
        </Button>
      </div>
    </div>
  );
}
