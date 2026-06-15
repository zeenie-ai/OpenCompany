import * as React from 'react';

/** Modal — overlay dialog with flat scrim, title bar, optional footer. */
export interface ModalProps {
  /** Default true */
  open?: boolean;
  title: React.ReactNode;
  children?: React.ReactNode;
  /** Footer actions (right-aligned), e.g. Cancel + primary Button */
  footer?: React.ReactNode;
  onClose?: () => void;
  /** Max card width in px. Default 440 */
  width?: number;
  /** Render only the dialog card, no fixed overlay (for specimens) */
  inline?: boolean;
  style?: React.CSSProperties;
}

export function Modal(props: ModalProps): JSX.Element | null;
