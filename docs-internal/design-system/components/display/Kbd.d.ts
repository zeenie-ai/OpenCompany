import * as React from 'react';

/** Kbd — keyboard shortcut chip (mono, 2px bottom edge). */
export interface KbdProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
}

export function Kbd(props: KbdProps): JSX.Element;
