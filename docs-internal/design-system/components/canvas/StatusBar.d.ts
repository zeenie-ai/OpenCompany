import * as React from 'react';

/** StatusBar — 24px mono shell-prompt footer with connection pip and live clock. */
export interface StatusBarProps {
  /** Connection state. Default 'online' */
  connection?: 'online' | 'connecting' | 'offline';
  workflowName?: string;
  nodeCount?: number;
  /** Uppercase theme label, e.g. 'DARK'. Default 'DARK' */
  themeName?: string;
  /** Fixed clock text — omit for a live ticking clock */
  clock?: string;
  style?: React.CSSProperties;
}

export function StatusBar(props: StatusBarProps): JSX.Element;
