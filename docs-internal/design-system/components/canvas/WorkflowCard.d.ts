import * as React from 'react';

/** WorkflowCard — sidebar saved-workflow card; selected = 3px accent left edge. */
export interface WorkflowCardProps {
  name: string;
  nodeCount?: number;
  /** Formatted timestamp, e.g. "Jun 11, 09:14" */
  modified?: string;
  selected?: boolean;
  onClick?: React.MouseEventHandler;
  style?: React.CSSProperties;
}

export function WorkflowCard(props: WorkflowCardProps): JSX.Element;
