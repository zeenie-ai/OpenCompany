import * as React from 'react';

/** ComponentItem — draggable palette card (icon tile, name, description, grip). */
export interface ComponentItemProps {
  /** Icon node or emoji string; falls back to 📦 */
  icon?: React.ReactNode;
  name: string;
  description?: string;
  onClick?: React.MouseEventHandler;
  style?: React.CSSProperties;
}

export function ComponentItem(props: ComponentItemProps): JSX.Element;
