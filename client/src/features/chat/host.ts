/**
 * What a host gives the chat (ChatPane): Home's employee page and Dev's
 * console Chat pane. The chat owns the thread, the turns and the message
 * box; the host owns everything around them (who answers, whether a message
 * can go now, what to act on, how to tell the owner something).
 */

import type { ReactNode } from 'react';
import type { ColorRole } from '@/components/catalog/presentation';
import type { ArtifactRef } from './data/parts';
import type { ThreadScope } from './data/thread';

export type ChatHostKind = 'home' | 'dev';

/** Who answers in this chat. */
export interface ChatPersona {
  name: string;
  colorRole: ColorRole;
  /** Their photo, when the owner gave them one. */
  photo?: string | null;
}

/**
 * The message box:
 * - `send`: a message goes to them now;
 * - `queue`: it waits until they are resumed;
 * - `closed`: nothing can read it, so there is no box (the host's notices
 *   say why and offer what to do).
 */
export type ComposerMode = 'send' | 'queue' | 'closed';

export type NotifyTone = 'info' | 'success' | 'error';

export interface ChatHost {
  kind: ChatHostKind;
  /** The chat session: the workflow id, or `default` with none open. */
  sessionId: string;
  scope: ThreadScope;
  persona: ChatPersona;
  composer: ComposerMode;
  /** Above the box, while there is something to act on (Start, Apply, Help
   *  in browser). */
  notices?: ReactNode;
  /** The first item of the thread's scroll area (Home: the orb). */
  top?: ReactNode;
  /** After the conversation, inside the scroll area (Home: the drafts
   *  waiting for the owner). */
  afterThread?: ReactNode;
  /** In place of the conversation while it has no message. */
  emptyState?: ReactNode;
  /** One line under the box. */
  footnote?: ReactNode;
  /** A short message for the owner (Home: the pill toast; Dev: a toast). */
  notify: (message: string, tone: NotifyTone) => void;
  /** A message did not go: the server's code (`not_running`,
   *  `run_in_progress`, `save_failed`, ...; `transport` when it may not have
   *  reached the server). Its text is back in the box. Without this the
   *  owner hears that it did not send. */
  onSendRefused?: (code: string) => void;
  /** Said on the working turn's status line (Home: the agent waiting to
   *  retry after a failed attempt). */
  liveNote?: string | null;
  /** The thread left its top, or came back to it (Home's header border). */
  onScrolledChange?: (scrolled: boolean) => void;
  /** The console pane: denser type and spacing. */
  compact?: boolean;
  /** Show a document the employee wrote on its Canvas (Home: the Workspace's
   *  Canvas tab; Dev: the Canvas dock). Without it the reply's card cannot
   *  open it. */
  openArtifact?: (artifact: ArtifactRef) => void;
}

/** What a host can ask of a mounted chat. */
export interface ChatPaneHandle {
  focusComposer: () => void;
}
