import * as React from 'react';

/** Slider — range control (settings compaction ratio, volumes). */
export interface SliderProps {
  value?: number;
  defaultValue?: number;
  min?: number;
  max?: number;
  step?: number;
  onChange?: (value: number) => void;
  /** Fill + thumb ring color. Default var(--primary) */
  color?: string;
  disabled?: boolean;
  style?: React.CSSProperties;
}

export function Slider(props: SliderProps): JSX.Element;
