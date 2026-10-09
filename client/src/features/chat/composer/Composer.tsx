/**
 * The message box (design handoff chat, "Composer"): a text box that grows
 * with what is written, up to `--h-chat-composer-max`, then scrolls; and
 * Send, which becomes Stop while the employee is answering. Enter sends,
 * Shift+Enter starts a new line, and the Enter that confirms an input
 * method's composition never sends (lib/composerKeys). Esc stops an answer
 * (ChatPane); ArrowUp in an empty box edits the owner's last message.
 *
 * Where the chat allows them: files (Attach, a paste, or a drop on the chat,
 * all through `onAddFiles`), shown above the text as they upload; a
 * microphone that dictates into the box (composer/VoiceRecorder.tsx); and
 * slash commands, a list that opens while the box holds one word starting
 * with `/` (composer/SlashMenu.tsx); and, on Home, the model picker's
 * button before the microphone (composer/ModelPicker.tsx), with the host's
 * `frame` wrapping the box in the picker, whose panel opens above it. A
 * message can be files alone; Send waits while one is still uploading.
 *
 * What is written lives in the composer store per conversation, so it
 * survives switching to another employee and back. While the employee is
 * still answering the last message the box takes text but Send waits.
 */

import { ArrowUp, Mic, Play, Plus, Square } from 'lucide-react';
import {
  useId,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
  type RefObject,
} from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { isSendKey } from '@/lib/composerKeys';
import { useAutoGrow } from '@/lib/useAutoGrow';
import { cn } from '@/lib/utils';
import type { ChatCommand } from '../data/chatContext';
import type { NotifyTone } from '../host';
import { useAttachmentStore, useBoxAttachments } from '../state/attachmentStore';
import { useComposerDraft, useComposerStore } from '../state/composerStore';
import { AttachmentChips } from './AttachmentChips';
import { filesOf } from './attachments';
import { matchCommands, slashQuery } from './slash';
import { SlashMenu } from './SlashMenu';
import { VoiceRecorder } from './VoiceRecorder';

const NO_COMMANDS: readonly ChatCommand[] = [];

export interface ComposerProps {
  sessionId: string;
  name: string;
  /** The conversation has loaded, so a message can go after it. */
  ready: boolean;
  /** A message is on its way, or the employee is still answering one. */
  busy: boolean;
  onSend: () => void;
  /** Stops the answer under way; absent while there is none. */
  onStop?: () => void;
  onResume?: () => void;
  resuming?: boolean;
  /** The answer is already stopping. */
  stopping?: boolean;
  compact: boolean;
  /** The text box, for the host's focus requests. */
  boxRef: RefObject<HTMLTextAreaElement | null>;
  /** Chips beside the button (Web, Ask first). */
  chips?: ReactNode;
  /** ArrowUp in an empty box: edit the owner's last message. False when
   *  there is none to edit, so the key does what it always does. */
  onEditLast?: () => boolean;
  /** Files can go with a message: Attach and a paste add them here. */
  onAddFiles?: (files: File[]) => void;
  /** Dictation, where a speech provider can turn a recording into text. */
  dictation?: { workflowId: string; notify: (message: string, tone: NotifyTone) => void } | null;
  /** The slash commands the box offers. */
  commands?: readonly ChatCommand[];
  /** The box takes nothing yet (the host's `wait`). */
  disabled?: boolean;
  /** In place of "Message {name}…". */
  placeholder?: string;
  /** The text box gained or lost the cursor. */
  onFocusChange?: (focused: boolean) => void;
  /** The model picker's button, before the microphone. */
  picker?: ReactNode;
  /** Wraps the box itself (its border), which holds `picker`: the model
   *  picker's panel opens against it. */
  frame?: (box: ReactElement) => ReactNode;
}

export function Composer({
  sessionId,
  name,
  ready,
  busy,
  onSend,
  onStop,
  onResume,
  resuming = false,
  stopping = false,
  compact,
  boxRef,
  chips,
  onEditLast,
  onAddFiles,
  dictation = null,
  commands = NO_COMMANDS,
  disabled = false,
  placeholder,
  onFocusChange,
  picker,
  frame,
}: ComposerProps) {
  const draft = useComposerDraft(sessionId);
  const setText = useComposerStore((state) => state.setText);
  const attachments = useBoxAttachments(sessionId);
  const fileRef = useRef<HTMLInputElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const [recording, setRecording] = useState(false);
  useAutoGrow(boxRef, draft.text);

  const uploading = attachments.some((item) => item.state === 'uploading');
  const finished = attachments.some((item) => item.state === 'ready');
  const canSend = ready && !disabled && !busy && !uploading && (draft.text.trim().length > 0 || finished);

  // The slash list: open while the box is one word starting with `/` that
  // begins a command, until Esc (for that text) or a pick.
  const query = slashQuery(draft.text);
  const items = query === null ? NO_COMMANDS : matchCommands(commands, query);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const [lastQuery, setLastQuery] = useState(query);
  if (query !== lastQuery) {
    setLastQuery(query);
    setActive(0);
  }
  const menuOpen = !recording && items.length > 0 && dismissed !== draft.text;
  const highlighted = Math.min(active, Math.max(0, items.length - 1));

  const pick = (command: ChatCommand) => {
    setText(sessionId, command.fill);
    setDismissed(command.fill);
    requestAnimationFrame(() => {
      const box = boxRef.current;
      if (!box) return;
      box.focus();
      box.setSelectionRange(command.fill.length, command.fill.length);
    });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const plain = !(event.shiftKey || event.ctrlKey || event.metaKey || event.altKey);
    if (menuOpen) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const step = event.key === 'ArrowDown' ? 1 : -1;
        setActive((highlighted + step + items.length) % items.length);
        return;
      }
      if ((event.key === 'Enter' || event.key === 'Tab') && plain && !event.nativeEvent.isComposing) {
        event.preventDefault();
        pick(items[highlighted]);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setDismissed(draft.text);
        return;
      }
    }
    if (event.key === 'ArrowUp' && !draft.text && plain && onEditLast?.()) {
      event.preventDefault();
      return;
    }
    if (!isSendKey(event)) return;
    event.preventDefault();
    if (canSend) onSend();
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = filesOf(event.clipboardData);
    if (!onAddFiles || files.length === 0) return;
    event.preventDefault();
    onAddFiles(files);
  };

  const iconSize = compact ? 'size-3.5' : 'size-4';
  const round = cn('shrink-0 rounded-full', compact ? 'size-7' : 'size-8.5');
  const sendButton = onResume ? (
    <Button variant="invert" size="icon" disabled={resuming} onClick={onResume}
      aria-label={resuming ? 'Resuming' : 'Resume'} title="Resume from the next pending action" className={round}>
      <Play aria-hidden fill="currentColor" className={iconSize} />
    </Button>
  ) : onStop ? (
    <Button
      variant="invert"
      size="icon"
      disabled={stopping}
      onClick={onStop}
      aria-label={stopping ? 'Stopping' : 'Stop reply'}
      title={stopping ? 'Stopping…' : 'Stop (Esc)'}
      className={round}
    >
      <Square aria-hidden fill="currentColor" strokeWidth={0} className={compact ? 'size-2.75' : 'size-3'} />
    </Button>
  ) : (
    <Button
      variant="invert"
      size="icon"
      disabled={!canSend}
      onClick={onSend}
      aria-label="Send"
      title={busy ? `${name} is still answering` : uploading ? 'Waiting for the files to upload' : 'Send (Enter)'}
      className={round}
    >
      <ArrowUp aria-hidden strokeWidth={2.25} className={iconSize} />
    </Button>
  );
  const attachButton = onAddFiles ? (
    <>
      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        onChange={(event) => {
          const files = filesOf(event.target);
          event.target.value = '';
          if (files.length) onAddFiles(files);
        }}
      />
      <Button
        variant="quiet"
        size="icon"
        disabled={recording}
        onClick={() => fileRef.current?.click()}
        aria-label="Attach files"
        title="Attach files"
        className={cn(round, 'border-border-default')}
      >
        <Plus aria-hidden className={iconSize} strokeWidth={2} />
      </Button>
    </>
  ) : null;
  const micButton =
    dictation && !recording && !onStop ? (
      <Button variant="quiet" size="icon" onClick={() => setRecording(true)} aria-label="Dictate" title="Dictate" className={round}>
        <Mic aria-hidden className={iconSize} strokeWidth={1.9} />
      </Button>
    ) : null;

  const box = recording && dictation ? (
    <VoiceRecorder
      sessionId={sessionId}
      workflowId={dictation.workflowId}
      compact={compact}
      notify={dictation.notify}
      onClose={() => {
        setRecording(false);
        requestAnimationFrame(() => boxRef.current?.focus());
      }}
      onText={(text) => {
        const current = useComposerStore.getState().drafts[sessionId]?.text ?? '';
        setText(sessionId, current.trim() ? `${current.trimEnd()} ${text}` : text);
      }}
    />
  ) : (
    <Textarea
      ref={boxRef}
      variant="bare"
      rows={1}
      value={draft.text}
      disabled={disabled}
      onChange={(event) => setText(sessionId, event.target.value)}
      onKeyDown={onKeyDown}
      onPaste={onPaste}
      onFocus={() => onFocusChange?.(true)}
      onBlur={() => onFocusChange?.(false)}
      aria-label={`Message ${name}`}
      aria-autocomplete={commands.length ? 'list' : undefined}
      placeholder={placeholder ?? (commands.length ? `Message ${name}…  Type / for commands` : `Message ${name}…`)}
      className={cn(
        'max-h-(--h-chat-composer-max) overflow-hidden text-fg-default field-sizing-fixed',
        compact ? 'py-1 text-sm leading-normal' : 'py-1.5 text-md leading-normal',
      )}
    />
  );
  const files = <AttachmentChips items={attachments} onRemove={(id) => useAttachmentStore.getState().remove(sessionId, id)} />;

  const shell = (
    <div
      ref={shellRef}
      className={cn(
        'chat-composer relative flex border border-border-default bg-bg-panel transition-colors duration-(--dur-slow) focus-within:border-border-strong',
        compact ? 'flex-col gap-1.5 rounded-lg py-1.5 pr-1.5 pl-3' : 'flex-col gap-2 rounded-card py-2.5 pr-2.5 pb-2 pl-3.5 shadow-modal',
      )}
    >
      {attachments.length > 0 && <div className="pt-1">{files}</div>}
      {compact ? (
        <div className="flex items-end gap-2">
          <div className="min-w-0 flex-1">{box}</div>
          <div className="flex shrink-0 items-center gap-1.5">
            {attachButton}
            {chips}
            {micButton}
            {!recording && sendButton}
          </div>
        </div>
      ) : (
        <>
          {box}
          <div className="flex items-center gap-1.5">
            {attachButton}
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">{chips}</div>
            {!recording && picker}
            {micButton}
            {!recording && sendButton}
          </div>
        </>
      )}
    </div>
  );

  return (
    <>
      {frame ? frame(shell) : shell}
      <SlashMenu
        open={menuOpen}
        listId={listId}
        items={items}
        active={highlighted}
        boxRef={boxRef}
        anchorRef={shellRef}
        onPick={pick}
        onActive={setActive}
        onDismiss={() => setDismissed(draft.text)}
      />
    </>
  );
}
