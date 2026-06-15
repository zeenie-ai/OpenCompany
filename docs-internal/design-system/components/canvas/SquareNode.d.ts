import * as React from 'react';

/**
 * SquareNode — MachinaOS canvas node: accent-bordered icon square with
 * status pip, gear button, connection handles, and label below.
 *
 * @startingPoint section="Canvas" subtitle="Workflow canvas node with pip, gear and handles" viewport="700x220"
 */
export interface SquareNodeProps {
  /** Icon node — emoji string, <Icon/>, or <img> (~28px) */
  icon?: React.ReactNode;
  /** Label rendered below the square */
  label?: string;
  /** Node accent color — use a node role token, e.g. 'var(--node-agent)'. Default dracula cyan */
  color?: string;
  /** Status pip state. 'listening' = trigger armed/waiting (continuous breathing glow). Default 'idle' */
  status?: 'idle' | 'executing' | 'listening' | 'waiting' | 'success' | 'error';
  /** Selected = solid accent border + ring */
  selected?: boolean;
  /** Executing = three-layer pulse glow (needs tokens/animations.css) */
  executing?: boolean;
  /** Trigger node: no input handle, lightning ⚡ badge; pair with status="listening" for the armed pulse */
  trigger?: boolean;
  /** Glow/pulse color — theme-contrast, independent of the fill `color`. Defaults to `color`. Set to a high-contrast accent for visibility on the active theme background. */
  pulseColor?: string;
  showGear?: boolean;
  showInput?: boolean;
  showOutput?: boolean;
  /** Top handle — for tool-capable nodes connecting to agents */
  showToolOutput?: boolean;
  /** Square size in px. Default 64 */
  size?: number;
  onClick?: React.MouseEventHandler;
  onGearClick?: React.MouseEventHandler;
  style?: React.CSSProperties;
}

export function SquareNode(props: SquareNodeProps): JSX.Element;
