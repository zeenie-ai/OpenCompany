import * as React from 'react';

/** DataCard — execution-data card (Input/Output columns of the parameter panel). */
export interface DataCardProps {
  /** e.g. "Item 1" or "Execution Result" */
  title: React.ReactNode;
  /** Source/status chip, e.g. "from WhatsApp Receive" or "Success · 1.9s" */
  badge?: React.ReactNode;
  /** Colors the left edge, icon and badge. Default 'success' */
  tone?: 'success' | 'error' | 'warning';
  /** Label above the JSON block. Default "Received Data" */
  blockLabel?: string;
  /** Object (pretty-printed) or pre-formatted string */
  data: unknown;
  /** Override the default clipboard copy */
  onCopy?: (json: string) => void;
  style?: React.CSSProperties;
}

export function DataCard(props: DataCardProps): JSX.Element;
