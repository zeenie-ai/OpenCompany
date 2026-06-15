import * as React from 'react';

/** Progress — slim accent bar with optional label + mono percent. */
export interface ProgressProps {
  /** 0–100 */
  value: number;
  /** Fill color. Default var(--primary) */
  color?: string;
  /** Optional left label; percent renders right in mono */
  label?: React.ReactNode;
  style?: React.CSSProperties;
}

export function Progress(props: ProgressProps): JSX.Element;
