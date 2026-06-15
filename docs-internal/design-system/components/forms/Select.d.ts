import * as React from 'react';

/** Select — 32px dropdown with custom menu card. Options are strings or {value,label}. */
export interface SelectProps {
  options: Array<string | { value: string; label: React.ReactNode }>;
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  /** Default "Select..." — placeholder copy ends with "..." */
  placeholder?: string;
  disabled?: boolean;
  style?: React.CSSProperties;
  wrapperStyle?: React.CSSProperties;
}

export function Select(props: SelectProps): JSX.Element;
