import * as React from 'react';

/** Tabs — console-dock tab strip; active = accent text + 2px underline. */
export interface TabsProps {
  /** Tab list — strings or {id, label} objects */
  tabs: Array<string | { id: string; label: React.ReactNode }>;
  /** Active tab id */
  active: string;
  onChange?: (id: string) => void;
  style?: React.CSSProperties;
}

export function Tabs(props: TabsProps): JSX.Element;
