import * as React from 'react';

/** Spinner — rotating arc. */
export interface SpinnerProps {
  /** Pixel size. Default 16 */
  size?: number;
  /** Arc color. Default var(--primary) */
  color?: string;
  style?: React.CSSProperties;
}

export function Spinner(props: SpinnerProps): JSX.Element;
