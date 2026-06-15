import * as React from 'react';

/** Input — 32px text field with optional leading icon. */
export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  /** Leading icon node, e.g. <Icon name="Search" size={14} /> */
  icon?: React.ReactNode;
  /** Styles for the outer wrapper div */
  wrapperStyle?: React.CSSProperties;
}

export function Input(props: InputProps): JSX.Element;
