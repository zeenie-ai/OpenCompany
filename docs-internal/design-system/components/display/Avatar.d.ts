import * as React from 'react';

/** Avatar — initials in a soft accent tile (providers, agents, users). */
export interface AvatarProps {
  /** Full name; initials derived from first two words */
  name: string;
  /** Accent color. Default var(--accent) */
  color?: string;
  /** Pixel size. Default 28 */
  size?: number;
  /** Rounded square instead of circle */
  square?: boolean;
  style?: React.CSSProperties;
}

export function Avatar(props: AvatarProps): JSX.Element;
