import * as React from 'react';

/**
 * ActionButton — MachinaOS's signature soft-tinted toolbar button.
 * Semantic intent picks the Dracula accent: run=green (deploy/run),
 * stop=pink (destructive), save=cyan (commit/panels), config=orange
 * (settings), secret=yellow (credentials), tools=purple (palette).
 *
 * @startingPoint section="Components" subtitle="Soft tinted toolbar button with six semantic intents" viewport="700x180"
 */
export interface ActionButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Semantic role — picks the accent triplet. Default 'save' */
  intent?: 'run' | 'stop' | 'save' | 'config' | 'secret' | 'tools';
  /** Square 32px icon-only variant */
  iconOnly?: boolean;
  children?: React.ReactNode;
}

export function ActionButton(props: ActionButtonProps): JSX.Element;
