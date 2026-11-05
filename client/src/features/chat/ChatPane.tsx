/**
 * The chat shared by Home's employee page and Dev's console Chat pane
 * (design handoff chat/): the conversation, scrolling on its own, and under
 * it the dock with the host's notices, the message box and a footnote. The
 * host (`ChatHost`) says who answers, whether a message can go now and what
 * sits around the conversation; the chat owns the rest.
 *
 * The turns follow the session's runs (docs-internal/chat_protocol.md): the
 * employee's steps and answer stream in while the run a message started is
 * going, whatever else arrives meanwhile, and the next message waits until
 * it ends. Meanwhile the live work pill above the box says what they are
 * doing (composer/LiveWork.tsx), Send is Stop, and Esc anywhere in the pane
 * stops the answer too. A message that does not go comes back into the box.
 *
 * What the employee wants to send waits for the owner on a card: on the
 * reply that made it, or after the conversation for the employee's own work
 * (data/approvals.ts). Ctrl/Cmd+Enter sends the newest one; the Ask first
 * chip beside the box changes the rule for everything they send, or, where
 * the employee's generation asks by mode (RFC-0007 §4.2), the Ask first
 * menu picks the mode (composer/AskModeMenu.tsx). While the answer waits on
 * a card, a message goes as the owner's answer to it instead (D3).
 *
 * Files go with a message where the chat allows them: Attach, a paste, or
 * a drop anywhere on the chat (DropOverlay), all through one
 * `addAttachments` (composer/attachments.ts). The box also dictates, offers
 * slash commands and, in an empty chat, suggestions (data/chatContext.ts),
 * and the Web chip keeps the employee off web search for the next messages.
 * On Home, where the employee takes it, the model picker chooses the model
 * and how hard it thinks (composer/ModelPicker.tsx); a message waits until
 * that choice is known. Every message, edit and retry carries these choices
 * (`sendOptions`). In the host's `wait` the box shows but takes nothing yet.
 *
 * The owner can change the conversation (data/branches.ts, TurnActions):
 * edit one of their messages in place (ArrowUp in an empty box edits the
 * last one), try the latest answer again, move between the versions of a
 * message or an answer, and rate an answer. Each waits while a message is
 * on its way or the employee is answering; a refusal says why.
 */

import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type ReactElement,
  type Ref,
} from 'react';
import { cn } from '@/lib/utils';
import { ApprovalsContext, type ApprovalsValue } from './approval/context';
import { StandaloneApprovals } from './approval/StandaloneApprovals';
import { AskFirstChip } from './composer/AskFirstChip';
import { AskModeMenu } from './composer/AskModeMenu';
import { addAttachments } from './composer/attachments';
import { DropOverlay } from './composer/DropOverlay';
import { LiveWork } from './composer/LiveWork';
import { ModelPicker, ModelPickerButton } from './composer/ModelPicker';
import { Suggestions } from './composer/Suggestions';
import { WebChip } from './composer/WebChip';
import { canRecord, useChatContext, useDictation, type ChatCommand } from './data/chatContext';
import {
  NO_APPROVALS,
  isOpenApproval,
  turnApprovalIds,
  useApprovalEvents,
  useAskFirst,
  useChatApprovals,
  useDecideApproval,
  useSetAskFirst,
  waitsOnOwner,
  type AskMode,
  type DecideInput,
  type SetAskFirstResult,
} from './data/approvals';
import { useConversation } from './data/conversation';
import { useChatWorkflowControl, useResumeChatGeneration } from './data/control';
import {
  useEditChatMessage,
  useRegenerateChatReply,
  useSetChatFeedback,
  useSwitchChatBranch,
  type ChatBranchError,
} from './data/branches';
import { useChatChoice, useChatModels } from './data/models';
import { sendOptions, useSendChatMessage, type ChatSendError } from './data/send';
import { useStopChatRun } from './data/stop';
import { useUiStateSync } from './data/uiState';
import { Composer } from './composer/Composer';
import { asksWhatItSays, type ChatUiActions } from './genui/actions';
import type { ChatHost, ChatPaneHandle } from './host';
import { useAttachmentStore } from './state/attachmentStore';
import { newClientMessageId, useComposerStore } from './state/composerStore';
import { ChatThread } from './thread/ChatThread';
import { TurnActionsContext, type TurnActions } from './thread/turnActions';
import { branchRefusalText, feedbackThanks } from './turns/runCopy';

/** What a mode change tells the owner. */
const MODE_SET: Record<AskMode, string> = {
  outside: 'will ask before anything outside',
  everything: 'will ask before everything',
  money_only: 'will ask only before money and deletes',
};

export function ChatPane({ host, ref }: { host: ChatHost; ref?: Ref<ChatPaneHandle> }) {
  const { sessionId, scope, persona, composer, notify, onSendRefused, onSent, onComposerChange, compact = false } = host;
  const { thread, turns, lane } = useConversation(sessionId, scope);
  const workflowId = sessionId === 'default' ? null : sessionId;
  const control = useChatWorkflowControl(workflowId, lane);
  const controlled = control?.execution_control_version === 1;
  // What asks is the mode and the call's class (RFC-0007): the Ask first
  // menu in place of the chip, and Allowed without asking.
  const byMode = control?.approval_wait_version === 1;
  const suspended = controlled && control.state === 'paused';
  const resuming = controlled && control.state === 'resuming';
  const resume = useResumeChatGeneration(control, () => notify('Couldn’t resume. Try again.', 'error'));
  const boxRef = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focusComposer: () => boxRef.current?.focus() }), []);
  const focusBox = useCallback(() => boxRef.current?.focus(), []);

  // What the box offers here (files, dictation, commands, Web, the model
  // picker), and what the next message carries: Web off, and the picker's
  // choice where the employee takes one.
  const contextQuery = useChatContext(sessionId);
  const chatContext = contextQuery.data;
  const pickerHost = Boolean(host.modelPicker && workflowId);
  const takesChoice = pickerHost && chatContext?.modelChoice === true;
  const { choice, pending: choicePending, choose } = useChatChoice((message) => notify(message, 'error'));
  const modelsQuery = useChatModels(sessionId, takesChoice);
  // Until the choice is known a message waits, so it never goes on a model
  // the owner didn't pick.
  const choiceUnknown = pickerHost && (contextQuery.isPending || (takesChoice && choicePending));
  const web = useComposerStore((state) => state.web[sessionId] !== false);
  const options = sendOptions(web, takesChoice ? choice : null);
  const optionsRef = useRef(options);
  useLayoutEffect(() => {
    optionsRef.current = options;
  });

  const onRefused = useCallback(
    (error: ChatSendError) => {
      // The model chosen can't answer: the server says why.
      if (error.code === 'model_unavailable' && error.detail) notify(error.detail, 'error');
      else if (onSendRefused) onSendRefused(error.code);
      else notify('Your message didn’t send. Try again.', 'error');
    },
    [notify, onSendRefused],
  );
  const messageSent = useCallback(() => onSent?.('message'), [onSent]);
  const send = useSendChatMessage(sessionId, scope, onRefused, messageSent);
  const stop = useStopChatRun(sessionId, () => notify('Couldn’t stop the reply. Try again.', 'error'), control);

  // Changing the conversation: edit, try again, another version, a rating.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingIn, setEditingIn] = useState(sessionId);
  if (editingIn !== sessionId) {
    setEditingIn(sessionId);
    setEditingId(null);
  }
  const branchRefused = useCallback(
    (error: ChatBranchError) => {
      if (error.code === 'model_unavailable' && error.detail) {
        notify(error.detail, 'error');
        return;
      }
      if (error.code === 'not_running' && onSendRefused) {
        onSendRefused('not_running');
        return;
      }
      const quiet = error.code === 'revision_conflict' || error.code === 'run_in_progress';
      notify(branchRefusalText(error.code, persona.name), quiet ? 'info' : 'error');
    },
    [notify, onSendRefused, persona.name],
  );
  const edit = useEditChatMessage(sessionId, scope, branchRefused);
  const regenerate = useRegenerateChatReply(sessionId, scope, branchRefused);
  const switchBranch = useSwitchChatBranch(sessionId, scope, branchRefused);
  const feedback = useSetChatFeedback(sessionId, scope, () => notify('Your rating didn’t save. Try again.', 'error'));
  const changing = edit.isPending || regenerate.isPending || switchBranch.isPending;

  // What the employee wants to do and waits for the owner's OK on (cards).
  useApprovalEvents();
  const approvalsQuery = useChatApprovals(workflowId);
  const approvals = approvalsQuery.data ?? NO_APPROVALS;

  const busy = send.isPending || lane !== null || changing;
  const stopping = lane?.state === 'stopping' || stop.isPending || (controlled && control.state === 'pausing');
  // What the employee is doing, above the box, while the run works (not
  // while it waits to start or for Resume).
  const working = lane && lane.state !== 'queued' && !suspended && !resuming ? lane : null;
  // The lane's run waits for the owner's OK on a call: a message goes as
  // the owner's answer instead, declining the call with its text as the
  // note (RFC-0007 D3; the server decides). Nothing else goes meanwhile.
  const replying = lane !== null && waitsOnOwner(approvals, lane.runId);
  const sendBusy = replying ? send.isPending || changing : busy;

  const editMutate = edit.mutate;
  const regenerateMutate = regenerate.mutate;
  const switchMutate = switchBranch.mutate;
  const feedbackMutate = feedback.mutate;
  const turnActions = useMemo<TurnActions>(
    () => ({
      sessionId,
      editingId,
      busy,
      startEdit: (messageId) => setEditingId(messageId),
      cancelEdit: () => setEditingId(null),
      saveEdit: (messageId, text) =>
        editMutate(
          { messageId, text, clientMessageId: newClientMessageId(), options: optionsRef.current },
          { onSuccess: () => setEditingId(null) },
        ),
      switchTo: (messageId) => switchMutate({ messageId }),
      regenerate: (messageId) => regenerateMutate({ messageId, options: optionsRef.current }),
      rate: (messageId, value) =>
        feedbackMutate(
          { messageId, value },
          { onSuccess: () => value && notify(feedbackThanks(persona.name), 'success') },
        ),
    }),
    [sessionId, editingId, busy, editMutate, switchMutate, regenerateMutate, feedbackMutate, notify, persona.name],
  );

  // ArrowUp in an empty box edits the owner's last message, when it can be.
  const editLast = () => {
    if (busy) return false;
    const last = [...turns].reverse().find((turn) => turn.kind === 'user');
    if (!last || last.kind !== 'user' || !last.message.editable) return false;
    setEditingId(last.message.id);
    return true;
  };

  const ready = Boolean(thread.data) && !choiceUnknown;
  const submit = () => {
    const store = useComposerStore.getState();
    // An answer to a waiting call goes alone: files wait in the box.
    const box = replying ? [] : (useAttachmentStore.getState().boxes[sessionId] ?? []);
    const hasText = Boolean((store.drafts[sessionId]?.text ?? '').trim());
    const hasFiles = box.some((item) => item.state === 'ready');
    if (sendBusy || !ready || box.some((item) => item.state === 'uploading') || !(hasText || hasFiles)) return;
    const draft = store.takeForSend(sessionId);
    const attachments = replying ? [] : useAttachmentStore.getState().takeReady(sessionId);
    send.mutate({
      ...draft,
      text: draft.text.trim(),
      ...(attachments.length ? { attachments } : {}),
      ...(options ? { options } : {}),
      ...(replying ? { declines: true } : {}),
    });
  };

  const stopAnswer = () => {
    if (lane && !stopping && !suspended && !resuming) stop.mutate(lane);
  };

  // What an interface's buttons do. Read through a ref, so the handlers an
  // interface made once keep reaching the current pane.
  const uiState = useUiStateSync(sessionId);
  const live = useRef({ busy, ready, send: send.mutate, notify, uiState });
  useLayoutEffect(() => {
    live.current = { busy, ready, send: send.mutate, notify, uiState };
  });
  const uiActions = useMemo<ChatUiActions>(
    () => ({
      ask: (text, label) => {
        const pane = live.current;
        if (asksWhatItSays(text, label) && !pane.busy && pane.ready) {
          // What the owner set goes first, as it does before a press.
          pane.uiState.flush();
          pane.send({ text, clientMessageId: newClientMessageId(), options: optionsRef.current });
          return;
        }
        // Not what the button says (or not now): the owner reads it first.
        useComposerStore.getState().setText(sessionId, text);
        boxRef.current?.focus();
      },
      event: (event) => {
        const pane = live.current;
        if (pane.busy || !pane.ready) {
          pane.notify(`${persona.name} is still answering. Try that again once they finish.`, 'info');
          return;
        }
        pane.uiState.flush(event.partId);
        pane.send({
          text: event.label || event.action,
          clientMessageId: newClientMessageId(),
          uiEvent: { partId: event.partId, elementId: event.elementId, action: event.action, params: event.params },
          options: optionsRef.current,
        });
      },
    }),
    [sessionId, persona.name],
  );

  // A suggested question goes as written; while the last answer is still
  // coming, it waits in the box instead.
  const followUp = useCallback(
    (text: string) => {
      const pane = live.current;
      if (!pane.busy && pane.ready) {
        pane.send({ text, clientMessageId: newClientMessageId(), options: optionsRef.current });
        return;
      }
      useComposerStore.getState().setText(sessionId, text);
      boxRef.current?.focus();
    },
    [sessionId],
  );

  // Drafts waiting for the owner: on the replies that made them, and the
  // rest (the employee's own work) after the conversation.
  const askFirstQuery = useAskFirst(workflowId);
  const setAskFirst = useSetAskFirst(workflowId ?? '', (message) => notify(message, 'error'));
  const [deciding, setDeciding] = useState<ReadonlySet<string>>(() => new Set());
  const decideMutation = useDecideApproval(workflowId ?? '', (error) => notify(error.message, 'error'));
  const decide = useCallback(
    (input: DecideInput) => {
      const id = input.approval.id;
      setDeciding((current) => new Set(current).add(id));
      decideMutation.mutate(input, {
        onSuccess: () => {
          if (input.decision === 'send') onSent?.('draft');
          if (input.always && input.approval.always) notify(`Always allowed: ${input.approval.always.label}`, 'success');
        },
        onSettled: () =>
          setDeciding((current) => {
            const next = new Set(current);
            next.delete(id);
            return next;
          }),
      });
    },
    [decideMutation, onSent, notify],
  );
  const linked = useMemo(() => {
    const ids = new Set<string>();
    for (const turn of turns) {
      if (turn.kind !== 'assistant') continue;
      for (const id of turnApprovalIds(turn.message, turn.run, approvals)) ids.add(id);
    }
    return ids;
  }, [turns, approvals]);
  const standalone = useMemo(() => {
    const now = approvals.receivedAt + approvals.offsetMs;
    return [...approvals.order]
      .reverse()
      .filter((id) => !linked.has(id) && isOpenApproval(approvals.byId.get(id)!, now));
  }, [approvals, linked]);
  const approvalsValue = useMemo<ApprovalsValue>(
    () => ({ approvals, name: persona.name, compact, decide, deciding, byMode }),
    [approvals, persona.name, compact, decide, deciding, byMode],
  );

  // Ctrl/Cmd+Enter sends the newest draft still waiting for the owner, never
  // one that spends money or deletes.
  const sendNewestDraft = () => {
    const id = approvals.order.find((candidate) => approvals.byId.get(candidate)?.status === 'pending');
    const approval = id ? approvals.byId.get(id) : undefined;
    if (!approval || deciding.has(approval.id) || approval.approvalClass === 'money') return false;
    decide({ approval, decision: 'send' });
    return true;
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.defaultPrevented) return;
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey) {
      if (sendNewestDraft()) event.preventDefault();
      return;
    }
    if (event.key !== 'Escape' || !lane || stopping || suspended || resuming) return;
    event.preventDefault();
    stopAnswer();
  };

  // The box's cursor and text, for a host that reacts to them (Home's orb).
  const [focused, setFocused] = useState(false);
  const typed = useComposerStore((state) => Boolean(state.drafts[sessionId]?.text.trim()));
  useEffect(() => {
    onComposerChange?.({ focused, typed });
  }, [onComposerChange, focused, typed]);

  const dictationQuery = useDictation(sessionId, Boolean(workflowId) && composer === 'send' && canRecord());
  const addFiles = useCallback(
    (files: File[]) => {
      if (!workflowId) return;
      void addAttachments(sessionId, workflowId, files).then((problem) => {
        if (problem) notify(problem, 'info');
      });
    },
    [sessionId, workflowId, notify],
  );
  const filesAllowed = Boolean(workflowId && chatContext?.attachments && (composer === 'send' || composer === 'queue'));
  const fill = useCallback(
    (text: string) => {
      useComposerStore.getState().setText(sessionId, text);
      requestAnimationFrame(() => {
        const box = boxRef.current;
        if (!box) return;
        box.focus();
        box.setSelectionRange(text.length, text.length);
      });
    },
    [sessionId],
  );
  const fillBox = useCallback((command: ChatCommand) => fill(command.fill), [fill]);
  // Home only: the editor's chat is a console for trying chat triggers, where
  // a card about the employee in an empty chat read as something a Reset left.
  const suggestions = useMemo(
    () => (host.kind === 'home' ? (chatContext?.commands ?? []).filter((command) => command.suggest) : []),
    [chatContext, host.kind],
  );
  // What to say first, under the empty state, once a message can go.
  const starters = composer !== 'send' ? null : suggestions.length > 0 ? <Suggestions items={suggestions} onPick={fillBox} /> : null;

  // A drop anywhere on the chat adds the files, as Attach does.
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const carriesFiles = (event: DragEvent<HTMLElement>) => Array.from(event.dataTransfer?.types ?? []).includes('Files');
  const dropHandlers = filesAllowed
    ? {
        onDragEnter: (event: DragEvent<HTMLElement>) => {
          if (!carriesFiles(event)) return;
          event.preventDefault();
          dragDepth.current += 1;
          setDragging(true);
        },
        onDragOver: (event: DragEvent<HTMLElement>) => {
          if (carriesFiles(event)) event.preventDefault();
        },
        onDragLeave: () => {
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (dragDepth.current === 0) setDragging(false);
        },
        onDrop: (event: DragEvent<HTMLElement>) => {
          if (!carriesFiles(event)) return;
          event.preventDefault();
          dragDepth.current = 0;
          setDragging(false);
          addFiles(Array.from(event.dataTransfer.files));
        },
      }
    : {};

  const askFirst = askFirstQuery.data?.askFirst;
  const webChip =
    workflowId && chatContext?.web ? (
      <WebChip on={web} name={persona.name} compact={compact} onChange={(next) => useComposerStore.getState().setWeb(sessionId, next)} />
    ) : null;
  const ruleChanged = (result: SetAskFirstResult) => {
    if (result.needsApply) notify(`Apply ${persona.name}’s changes for this to cover everything.`, 'info');
    else if (byMode && result.mode) notify(`${persona.name} ${MODE_SET[result.mode]}`, 'success');
    else notify(result.askFirst ? `${persona.name} will ask before sending` : `${persona.name} will send without asking`, 'success');
  };
  const askFirstChip =
    !workflowId || !askFirstQuery.data ? null
    : byMode ? (
      <AskModeMenu
        workflowId={workflowId}
        mode={askFirstQuery.data.mode}
        name={persona.name}
        compact={compact}
        disabled={setAskFirst.isPending}
        onChange={(next) => setAskFirst.mutate(next, { onSuccess: ruleChanged })}
        onError={(message) => notify(message, 'error')}
      />
    ) : (
      <AskFirstChip
        on={askFirst === true}
        name={persona.name}
        compact={compact}
        disabled={setAskFirst.isPending}
        onChange={(next) => setAskFirst.mutate(next, { onSuccess: ruleChanged })}
      />
    );
  // The picker wraps the box wherever the host offers it, from the first
  // render, so the box isn't mounted again when the rows arrive; its button
  // shows once they have.
  const pickerFrame = pickerHost
    ? (box: ReactElement) => (
        <ModelPicker
          name={persona.name}
          models={takesChoice ? modelsQuery.data : undefined}
          choice={choice}
          onModel={(model) => choose({ model })}
          onEffort={(effort) => choose({ effort })}
          onOpen={() => void modelsQuery.refetch()}
          boxRef={boxRef}
        >
          {box}
        </ModelPicker>
      )
    : undefined;
  const chips =
    webChip || askFirstChip ? (
      <>
        {webChip}
        {askFirstChip}
      </>
    ) : null;

  return (
    <ApprovalsContext.Provider value={approvalsValue}>
    <TurnActionsContext.Provider value={turnActions}>
    <section
      aria-label={`Chat with ${persona.name}`}
      onKeyDown={onKeyDown}
      {...dropHandlers}
      className="relative flex min-h-0 w-full flex-1 flex-col"
    >
      {dragging && <DropOverlay name={persona.name} />}
      <ChatThread
        persona={persona}
        turns={turns}
        loading={thread.isPending}
        error={thread.isError && !thread.data}
        onRetry={() => void thread.refetch()}
        top={host.top}
        afterThread={
          standalone.length > 0 || host.afterThread ? (
            <>
              <StandaloneApprovals ids={standalone} />
              {host.afterThread}
            </>
          ) : null
        }
        emptyState={
          starters ? (
            <div className="flex flex-col items-center gap-4">
              {host.emptyState}
              {starters}
            </div>
          ) : (
            host.emptyState
          )
        }
        liveNote={host.liveNote}
        compact={compact}
        canStop={composer !== 'closed'}
        uiActions={uiActions}
        onUiStateChange={uiState.change}
        onFollowUp={composer === 'send' ? followUp : undefined}
        onOpenArtifact={host.openArtifact}
        onScrolledChange={host.onScrolledChange}
      />
      <div className={cn('relative flex-none', compact ? 'border-t border-border-default px-3 py-2' : 'px-6 pb-3')}>
        <div className={cn('mx-auto flex w-full flex-col', compact ? 'gap-2' : 'max-w-(--w-chat-column) gap-2.5')}>
          {host.notices}
          <LiveWork run={working} name={persona.name} stopping={stopping} compact={compact} onLeave={focusBox} />
          {composer !== 'closed' && (
            <Composer
              sessionId={sessionId}
              name={persona.name}
              ready={ready}
              busy={sendBusy}
              replying={replying}
              onSend={submit}
              onStop={lane && !suspended && !resuming ? stopAnswer : undefined}
              onResume={suspended || resuming ? () => resume.mutate() : undefined}
              resuming={resume.isPending || resuming}
              stopping={stopping}
              compact={compact}
              boxRef={boxRef}
              chips={chips}
              onEditLast={editLast}
              onAddFiles={filesAllowed ? addFiles : undefined}
              dictation={workflowId && dictationQuery.data ? { workflowId, notify } : null}
              commands={chatContext?.commands}
              disabled={composer === 'wait'}
              placeholder={host.placeholder}
              onFocusChange={setFocused}
              picker={pickerHost ? <ModelPickerButton /> : null}
              frame={pickerFrame}
            />
          )}
          {host.footnote && <p className="m-0 text-center text-xs text-fg-muted">{host.footnote}</p>}
        </div>
      </div>
    </section>
    </TurnActionsContext.Provider>
    </ApprovalsContext.Provider>
  );
}

export default ChatPane;
