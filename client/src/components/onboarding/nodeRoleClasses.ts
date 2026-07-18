/**
 * Shared role-to-Tailwind-classes map for the onboarding step cards.
 * Each role keys into the existing `--node-X` triplet (soft bg + border
 * + accent text). Co-located with the onboarding folder because all
 * consumers (the wizard steps) live here.
 */

export type NodeRole = 'model' | 'skill' | 'agent' | 'workflow' | 'trigger';

export interface NodeRoleClasses {
  /** Tinted background + border for the card surface. */
  card: string;
  /** Accent text colour matching the card's role. */
  text: string;
}

export const NODE_ROLE_CLASSES: Record<NodeRole, NodeRoleClasses> = {
  model:    { card: 'bg-node-model-soft border-node-model-border',       text: 'text-node-model' },
  skill:    { card: 'bg-node-skill-soft border-node-skill-border',       text: 'text-node-skill' },
  agent:    { card: 'bg-node-agent-soft border-node-agent-border',       text: 'text-node-agent' },
  workflow: { card: 'bg-node-workflow-soft border-node-workflow-border', text: 'text-node-workflow' },
  trigger:  { card: 'bg-node-trigger-soft border-node-trigger-border',   text: 'text-node-trigger' },
};
