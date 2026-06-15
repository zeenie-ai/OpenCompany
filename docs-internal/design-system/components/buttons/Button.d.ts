import * as React from 'react';

/**
 * Button — general-purpose shadcn-derived button. For toolbar actions
 * prefer ActionButton; Button covers dialogs, forms, and menus.
 */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Visual variant. Default 'default' (solid primary blue) */
  variant?: 'default' | 'outline' | 'secondary' | 'ghost' | 'destructive' | 'link';
  /** Size. icon / icon-sm are square. Default 'default' (32px) */
  size?: 'sm' | 'default' | 'lg' | 'icon' | 'icon-sm';
  children?: React.ReactNode;
}

export function Button(props: ButtonProps): JSX.Element;
