import * as React from 'react';

/** ModeToggle — segmented Normal/Dev toolbar control (Normal hides advanced nodes). */
export interface ModeToggleProps {
  /** Active mode. Default 'normal' */
  mode?: 'normal' | 'dev';
  onChange?: (mode: 'normal' | 'dev') => void;
  style?: React.CSSProperties;
}

export function ModeToggle(props: ModeToggleProps): JSX.Element;
