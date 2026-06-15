import * as React from 'react';

/** Tooltip — hover label; inverted capsule (fg-on-bg). */
export interface TooltipProps {
  label: React.ReactNode;
  /** Default 'top' */
  side?: 'top' | 'bottom';
  children: React.ReactNode;
  style?: React.CSSProperties;
}

export function Tooltip(props: TooltipProps): JSX.Element;
