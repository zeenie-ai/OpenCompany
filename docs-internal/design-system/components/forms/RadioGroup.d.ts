import * as React from 'react';

/** RadioGroup — radio list; options are strings or {value,label}. */
export interface RadioGroupProps {
  options: Array<string | { value: string; label: React.ReactNode }>;
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  /** 'column' (default) or 'row' */
  direction?: 'column' | 'row';
  disabled?: boolean;
  style?: React.CSSProperties;
}

export function RadioGroup(props: RadioGroupProps): JSX.Element;
