import * as React from 'react';

/** ChatBubble — dock Chat message; user right/primary-tint, agent left/card. */
export interface ChatBubbleProps {
  /** Default 'agent' */
  role?: 'user' | 'agent';
  /** Mono timestamp below the bubble */
  time?: string;
  children: React.ReactNode;
  style?: React.CSSProperties;
}

export function ChatBubble(props: ChatBubbleProps): JSX.Element;
