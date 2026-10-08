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
 * it ends. Meanwhile Send is Stop, and Esc anywhere in the pane stops the
 * answer too. A message that does not go comes back into the box.
 *
 * What the employee wants to send waits for the owner on a card: on the
 * reply that made it, or after the conversation for the employee's own work
 * (data/approvals.ts). Ctrl/Cmd+Enter sends the newest one; the Ask first
 * chip beside the box changes the rule for everything they send.
 *
 * Files go with a message where the chat allows them: Attach, a paste, or
 * a drop anywhere on the chat (DropOverlay), all through one
 * `addAttachments` (composer/attachments.ts). The box also dictates, offers
 * slash commands and, in an empty chat, the host's greetings or else
 * suggestions (data/chatContext.ts), and the Web chip keeps the employee off
 * web search for the next messages. In the host's `wait` the box shows but
 * takes nothing yet.
 *
 * The owner can change the conversation (data/branches.ts, TurnActions):
 * edit one of their messages in place (ArrowUp in an empty box edits the
 * last one), try the latest answer again, move between the versions of a
 * message or an answer, and rate an answer. Each waits while a message is
 * on its way or the employee is answering; a refusal says why.
 */

import {
  useCallback,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type Ref,
} from 'react';
import { cn } from '@/lib/utils';
import { ApprovalsContext, type ApprovalsValue } from './approval/context';
import { StandaloneApprovals } from './approval/StandaloneApprovals';
import { AskFirstChip } from './composer/AskFirstChip';
import { addAttachments } from './composer/attachments';
import { DropOverlay } from './composer/DropOverlay';
import { Greetings } from './composer/Greetings';
import { Suggestions } from './composer/Suggestions';
import { WebChip } from './composer/WebChip';
import { canRecord, useChatContext, useDictation, type ChatCommand } from './data/chatContext';
import {
  NO_APPROVALS,
  isOpenApproval,
  useApprovalEvents,
  useAskFirst,
  useChatApprovals,
  useDecideApproval,
  useSetAskFirst,
  type DecideInput,
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
import { liveApprovalIds, savedApprovalIds } from './data/parts';
import { useSendChatMessage, type ChatSendError } from './data/send';
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

export function ChatPane({ host, ref }: { host: ChatHost; ref?: Ref<ChatPaneHandle> }) {
  const { sessionId, scope, persona, composer, notify, onSendRefused, compact = false } = host;
  const { thread, turns, lane } = useConversation(sessionId, scope);
  const workflowId = sessionId === 'default' ? null : sessionId;
  const control = useChatWorkflowControl(workflowId, lane);
  const controlled = control?.execution_control_version === 1;
  const suspended = controlled && control.state === 'paused';
  const resuming = controlled && control.state === 'resuming';
  const resume = useResumeChatGeneration(control, () => notify('Couldn’t resume. Try again.', 'error'));
  const boxRef = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focusComposer: () => boxRef.current?.focus() }), []);

  const onRefused = useCallback(
    (error: ChatSendError) => {
      if (onSendRefused) onSendRefused(error.code);
      else notify('Your message didn’t send. Try again.', 'error');
    },
    [notify, onSendRefused],
  );
  const send = useSendChatMessage(sessionId, scope, onRefused);
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

  const busy = send.isPending || lane !== null || changing;
  const stopping = lane?.state === 'stopping' || stop.isPending || (controlled && control.state === 'pausing');

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
        editMutate({ messageId, text, clientMessageId: newClientMessageId() }, { onSuccess: () => setEditingId(null) }),
      switchTo: (messageId) => switchMutate({ messageId }),
      regenerate: (messageId) => regenerateMutate({ messageId }),
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

  const submit = () => {
    const store = useComposerStore.getState();
    const box = useAttachmentStore.getState().boxes[sessionId] ?? [];
    const hasText = Boolean((store.drafts[sessionId]?.text ?? '').trim());
    const hasFiles = box.some((item) => item.state === 'ready');
    if (busy || !thread.data || box.some((item) => item.state === 'uploading') || !(hasText || hasFiles)) return;
    const draft = store.takeForSend(sessionId);
    const attachments = useAttachmentStore.getState().takeReady(sessionId);
    const web = store.web[sessionId];
    send.mutate({
      ...draft,
      text: draft.text.trim(),
      ...(attachments.length ? { attachments } : {}),
      ...(web === false ? { options: { web: false } } : {}),
    });
  };

  const stopAnswer = () => {
    if (lane && !stopping && !suspended && !resuming) stop.mutate(lane);
  };

  // What an interface's buttons do. Read through a ref, so the handlers an
  // interface made once keep reaching the current pane.
  const uiState = useUiStateSync(sessionId);
  const live = useRef({ busy, ready: Boolean(thread.data), send: send.mutate, notify, uiState });
  useLayoutEffect(() => {
    live.current = { busy, ready: Boolean(thread.data), send: send.mutate, notify, uiState };
  });
  const uiActions = useMemo<ChatUiActions>(
    () => ({
      ask: (text, label) => {
        const pane = live.current;
        if (asksWhatItSays(text, label) && !pane.busy && pane.ready) {
          pane.send({ text, clientMessageId: newClientMessageId() });
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
        pane.send({ text, clientMessageId: newClientMessageId() });
        return;
      }
      useComposerStore.getState().setText(sessionId, text);
      boxRef.current?.focus();
    },
    [sessionId],
  );

  // Drafts waiting for the owner: on the replies that made them, and the
  // rest (the employee's own work) after the conversation.
  useApprovalEvents();
  const approvalsQuery = useChatApprovals(workflowId);
  const approvals = approvalsQuery.data ?? NO_APPROVALS;
  const askFirstQuery = useAskFirst(workflowId);
  const setAskFirst = useSetAskFirst(workflowId ?? '', (message) => notify(message, 'error'));
  const [deciding, setDeciding] = useState<ReadonlySet<string>>(() => new Set());
  const decideMutation = useDecideApproval(workflowId ?? '', (error) => notify(error.message, 'error'));
  const decide = useCallback(
    (input: DecideInput) => {
      const id = input.approval.id;
      setDeciding((current) => new Set(current).add(id));
      decideMutation.mutate(input, {
        onSettled: () =>
          setDeciding((current) => {
            const next = new Set(current);
            next.delete(id);
            return next;
          }),
      });
    },
    [decideMutation],
  );
  const linked = useMemo(() => {
    const ids = new Set<string>();
    for (const turn of turns) {
      if (turn.kind !== 'assistant') continue;
      for (const id of savedApprovalIds(turn.message?.parts)) ids.add(id);
      for (const id of liveApprovalIds(turn.run?.activities)) ids.add(id);
    }
    return ids;
  }, [turns]);
  const standalone = useMemo(() => {
    const now = approvals.receivedAt + approvals.offsetMs;
    return [...approvals.order]
      .reverse()
      .filter((id) => !linked.has(id) && isOpenApproval(approvals.byId.get(id)!, now));
  }, [approvals, linked]);
  const approvalsValue = useMemo<ApprovalsValue>(
    () => ({ approvals, name: persona.name, compact, decide, deciding }),
    [approvals, persona.name, compact, decide, deciding],
  );

  // Ctrl/Cmd+Enter sends the newest draft still waiting for the owner.
  const sendNewestDraft = () => {
    const id = approvals.order.find((candidate) => approvals.byId.get(candidate)?.status === 'pending');
    const approval = id ? approvals.byId.get(id) : undefined;
    if (!approval || deciding.has(approval.id)) return false;
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

  // What the box offers here: files, dictation, commands, Web.
  const contextQuery = useChatContext(sessionId);
  const chatContext = contextQuery.data;
  const dictationQuery = useDictation(sessionId, Boolean(workflowId) && composer === 'send' && canRecord());
  const web = useComposerStore((state) => state.web[sessionId] !== false);
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
  const starters =
    composer !== 'send' ? null
    : host.greetings?.length ? <Greetings items={host.greetings} onPick={fill} />
    : suggestions.length > 0 ? <Suggestions items={suggestions} onPick={fillBox} />
    : null;

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
  const askFirstChip =
    workflowId && askFirstQuery.data ? (
      <AskFirstChip
        on={askFirst === true}
        name={persona.name}
        compact={compact}
        disabled={setAskFirst.isPending}
        onChange={(next) =>
          setAskFirst.mutate(next, {
            onSuccess: (result) => {
              if (result.needsApply) notify(`Apply ${persona.name}’s changes for this to cover everything.`, 'info');
            },
          })
        }
      />
    ) : null;
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
          {composer !== 'closed' && (
            <Composer
              sessionId={sessionId}
              name={persona.name}
              ready={Boolean(thread.data)}
              busy={busy}
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
