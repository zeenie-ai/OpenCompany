import * as React from 'react';

/** Badge — small count or label chip. */
export interface BadgeProps {
  /** Default 'secondary'. 'accent' uses the soft-tint pattern with `color`. */
  variant?: 'default' | 'secondary' | 'outline' | 'accent';
  /** Use mono font (for counts/numbers). Default false */
  mono?: boolean;
  /** Accent color for variant="accent" — pass a token like 'var(--dracula-purple)' */
  color?: string;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}

export function Badge(props: BadgeProps): JSX.Element;
