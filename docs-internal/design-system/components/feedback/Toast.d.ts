import * as React from 'react';

/** Toast — status notification with pip, soft tint, mono timestamp. */
export interface ToastProps {
  /** Default 'info' */
  tone?: 'success' | 'error' | 'warning' | 'info';
  title: React.ReactNode;
  message?: React.ReactNode;
  /** Mono timestamp, e.g. "12:04:05" */
  time?: string;
  onClose?: () => void;
  style?: React.CSSProperties;
}

export function Toast(props: ToastProps): JSX.Element;
