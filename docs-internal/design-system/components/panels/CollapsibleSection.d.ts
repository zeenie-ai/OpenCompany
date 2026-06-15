import * as React from 'react';

/** CollapsibleSection — accordion card with elevated trigger head + rotating chevron. */
export interface CollapsibleSectionProps {
  title: React.ReactNode;
  /** Uncontrolled initial state. Default false (open) */
  defaultCollapsed?: boolean;
  /** Controlled collapsed state */
  collapsed?: boolean;
  /** Called with the next collapsed value */
  onToggle?: (collapsed: boolean) => void;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}

export function CollapsibleSection(props: CollapsibleSectionProps): JSX.Element;
