import * as React from 'react';

/**
 * PanelModal — workspace modal shell: title absolute-left, ActionButton
 * cluster centered, X absolute-right. Used by Node Configuration,
 * Settings, AI Result, and other large panels.
 */
export interface PanelModalProps {
  /** Default true */
  open?: boolean;
  title: React.ReactNode;
  /** Small icon before the title (renders at 70% opacity) */
  titleIcon?: React.ReactNode;
  /** Centered header cluster — node name + ActionButtons */
  headerActions?: React.ReactNode;
  children?: React.ReactNode;
  onClose?: () => void;
  /** CSS max-width. Default '90%' (product uses 95vw for big panels) */
  maxWidth?: string;
  /** CSS max-height. Default '88%' */
  maxHeight?: string;
  /** Render only the card, no fixed overlay (for specimens) */
  inline?: boolean;
  style?: React.CSSProperties;
}

export function PanelModal(props: PanelModalProps): JSX.Element | null;
