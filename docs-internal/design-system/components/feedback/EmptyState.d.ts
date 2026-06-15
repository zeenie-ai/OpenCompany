import * as React from 'react';

/** EmptyState — dashed placeholder with icon, title, hint, optional action. */
export interface EmptyStateProps {
  /** Icon node, e.g. <Icon name="FolderOpen" size={32} /> */
  icon?: React.ReactNode;
  title: React.ReactNode;
  hint?: React.ReactNode;
  /** Action node, usually a Button */
  action?: React.ReactNode;
  style?: React.CSSProperties;
}

export function EmptyState(props: EmptyStateProps): JSX.Element;
