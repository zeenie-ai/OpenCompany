import * as React from 'react';

/**
 * Icon — renders a Lucide icon by name from the lucide UMD CDN bundle.
 * Requires <script src="https://unpkg.com/lucide@latest/dist/umd/lucide.min.js"> on the page.
 */
export interface IconProps extends React.SVGAttributes<SVGSVGElement> {
  /** Lucide icon name, e.g. "Play", "Settings", "KeyRound" (PascalCase or kebab-case) */
  name: string;
  /** Pixel size (width = height). Default 16 */
  size?: number;
  /** Stroke width. Default 2 */
  strokeWidth?: number;
}

export function Icon(props: IconProps): JSX.Element;
