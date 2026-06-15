import * as React from 'react';

/** Card — base surface with optional hover-lift and selected (accent left edge) states. */
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Hover lifts -1px with deeper shadow + tint */
  interactive?: boolean;
  /** Selected = 3px accent left border + active background */
  selected?: boolean;
  children?: React.ReactNode;
}

export function Card(props: CardProps): JSX.Element;
