import * as React from 'react';

/** Textarea — multi-line input; mono variant for system prompts / code. */
export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** Use mono font (prompts, code, JSON). Default false */
  mono?: boolean;
}

export function Textarea(props: TextareaProps): JSX.Element;
