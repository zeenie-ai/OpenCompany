/**
 * What a chat's approval cards share (set by ChatPane): the session's drafts,
 * who answers, and how to decide a draft. A card outside a chat renders
 * nothing.
 */

import { createContext } from 'react';
import type { ChatApprovals, DecideInput } from '../data/approvals';

export interface ApprovalsValue {
  approvals: ChatApprovals;
  /** Who drafted them ("Maya wants to send ..."). */
  name: string;
  compact: boolean;
  decide: (input: DecideInput) => void;
  /** Drafts with a decision on its way to the server. */
  deciding: ReadonlySet<string>;
  /** The employee asks by mode (RFC-0007 §4.2): its Ask first menu lists
   *  what it does without asking. */
  byMode: boolean;
}

export const ApprovalsContext = createContext<ApprovalsValue | null>(null);
