/**
 * The Get started checklist's steps, in order (onboarding handoff D). What
 * each says under its label depends on the team, so useGetStarted writes it.
 */

import type { LucideIcon } from 'lucide-react';
import { KeyRound, MessageCircle, ShieldCheck, UserPlus } from 'lucide-react';

export type GetStartedItemId = 'connect-ai' | 'hire' | 'say-hello' | 'approve-draft';

export interface GetStartedItemDef {
  id: GetStartedItemId;
  label: string;
  icon: LucideIcon;
}

export const GET_STARTED_ITEMS: readonly GetStartedItemDef[] = [
  { id: 'connect-ai', label: 'Connect an AI model', icon: KeyRound },
  { id: 'hire', label: 'Hire your first employee', icon: UserPlus },
  { id: 'say-hello', label: 'Say hello', icon: MessageCircle },
  { id: 'approve-draft', label: 'Approve a first draft', icon: ShieldCheck },
];
