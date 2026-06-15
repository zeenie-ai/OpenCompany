import * as React from 'react';

/** LogLine — mono console row with faint [timestamp] and toned message. */
export interface LogLineProps {
  /** e.g. "12:04:03" */
  time?: string;
  /** Named tone or any CSS color. Default 'muted' */
  tone?: 'muted' | 'success' | 'error' | 'warning' | 'agent' | 'model' | 'tool' | 'trigger' | string;
  children: React.ReactNode;
  style?: React.CSSProperties;
}

export function LogLine(props: LogLineProps): JSX.Element;
