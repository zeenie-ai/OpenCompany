import * as React from 'react';

/** ApiKeyInput — credentials row: masked mono input + eye toggle + Validate/Valid button + optional delete. */
export interface ApiKeyInputProps {
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  /** Validate click */
  onSave?: () => void;
  /** Shows the red trash button when isStored */
  onDelete?: () => void;
  /** Default "Enter API key..." */
  placeholder?: string;
  /** Spinner in the button */
  loading?: boolean;
  /** Stored/valid state — green check + savedLabel */
  isStored?: boolean;
  disabled?: boolean;
  /** Default "Validate" */
  saveLabel?: string;
  /** Default "Valid" */
  savedLabel?: string;
  style?: React.CSSProperties;
}

export function ApiKeyInput(props: ApiKeyInputProps): JSX.Element;
