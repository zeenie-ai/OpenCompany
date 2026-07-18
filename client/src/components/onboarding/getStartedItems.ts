import type { LucideIcon } from 'lucide-react';
import { Sparkles, KeyRound, MessageCircle, Workflow, Palette } from 'lucide-react';

export type GetStartedItemId =
  | 'setup'
  | 'add-key'
  | 'chat-example'
  | 'build-workflow'
  | 'try-theme';

export interface GetStartedItemDef {
  id: GetStartedItemId;
  /** Verb-first label. */
  label: string;
  sublabel: string;
  icon: LucideIcon;
  /** Whether clicking the row performs an action (wired by Dashboard). */
  actionable: boolean;
}

/** The three seeded example workflow names — used to tell "built your own
 *  workflow" apart from editing a shipped example. */
export const EXAMPLE_WORKFLOW_NAMES = ['AI Assistant', 'AI Employee', 'Claude Assistant'];

export const GET_STARTED_ITEMS: GetStartedItemDef[] = [
  {
    id: 'setup',
    label: 'Set up your workspace',
    sublabel: 'OpenCompany is installed and running',
    icon: Sparkles,
    actionable: false,
  },
  {
    id: 'add-key',
    label: 'Add your AI key',
    sublabel: 'Connect OpenAI, Anthropic, or another provider',
    icon: KeyRound,
    actionable: true,
  },
  {
    id: 'chat-example',
    label: 'Chat with your AI Assistant',
    sublabel: 'Say hello to the ready-made example',
    icon: MessageCircle,
    actionable: true,
  },
  {
    id: 'build-workflow',
    label: 'Build your own workflow',
    sublabel: 'Start from a blank canvas and save it',
    icon: Workflow,
    actionable: true,
  },
  {
    id: 'try-theme',
    label: 'Try a new look',
    sublabel: 'Pick one of 12 visual themes',
    icon: Palette,
    actionable: false,
  },
];
