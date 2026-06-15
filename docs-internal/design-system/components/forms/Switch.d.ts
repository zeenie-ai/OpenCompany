import * as React from 'react';

/** Switch — 36×20 toggle; on = primary blue. Controlled via checked/onChange or uncontrolled via defaultChecked. */
export interface SwitchProps {
  checked?: boolean;
  defaultChecked?: boolean;
  /** Called with the next boolean value */
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  style?: React.CSSProperties;
}

export function Switch(props: SwitchProps): JSX.Element;
