/**
 * NodeIcon — single icon-rendering primitive.
 *
 * Resolves any backend-declared icon string (`lobehub:Claude`,
 * `asset:gmail`, `lucide:Battery`, emoji, URL, data URI) and renders
 * it inside a square whose edge is a design-system token (`size`):
 * `theme.nodeSize.squareIcon` on canvas nodes, `theme.iconSize.*`
 * everywhere else (`client/src/styles/theme.ts`). The token sets the
 * box's width, height AND font size, so the lucide / image branches
 * fill the square and the emoji/text branch is drawn at the same
 * edge. Callers never pass Tailwind sizing (`h-7`, `text-3xl`) — the
 * theme type scale maps `text-3xl` to 44px, which is how emoji nodes
 * once painted half again larger than every SVG-backed node.
 *
 * The wrapper does NOT apply a parent color to the resolved icon.
 * Each icon source carries its own color contract:
 *   - lobehub `.Color` SVGs: multi-color brand artwork (some paths
 *     use `currentColor` — applying a parent `color` would mono-tint
 *     the brand mark, which is wrong)
 *   - asset SVGs: explicit per-path fills (`<img>` is immune to
 *     parent CSS color)
 *   - lucide icons: stroke-based currentColor (used for monochrome
 *     glyphs only — backend nodes ship colored asset SVGs instead)
 *   - emoji / text: native glyph color
 *
 * Sites that need a tinted backdrop set `style={{ color: brandColor }}`
 * on their parent container alongside `bg-tint-soft` / `border-tint`;
 * NodeIcon sits inside without contributing to the color cascade.
 */

import * as React from 'react';

import { cn } from '@/lib/utils';
import { resolveIcon, resolveLibraryIcon, isImageIcon } from '.';

export interface NodeIconProps {
  /** Backend-declared icon string. May be undefined while the spec
   *  cache hydrates — the component renders the fallback in that case. */
  icon: string | undefined | null;
  /** Box edge as a design-system token (`theme.nodeSize.squareIcon`,
   *  `theme.iconSize.md`, ...). Drives width, height and the emoji
   *  font size together. */
  size: string;
  /** Layout-only extras (`shrink-0`). Never sizing or `text-*`. */
  className?: string;
  /** Element rendered when the icon ref does not resolve. */
  fallback?: React.ReactNode;
}

export const NodeIcon: React.FC<NodeIconProps> = ({
  icon,
  size,
  className,
  fallback = null,
}) => {
  // One token, three dimensions: the square's edge and the emoji font
  // size are the same length, so no icon kind can outgrow another.
  const box: React.CSSProperties = { width: size, height: size, fontSize: size };
  let inner: React.ReactNode;

  const LibIcon = resolveLibraryIcon(icon);
  if (LibIcon) {
    // LibIcon is a runtime-resolved component reference; using it as a JSX
    // tag trips react-hooks/static-components. createElement is equivalent
    // and rule-clean.
    inner = React.createElement(LibIcon, { className: 'h-full w-full' });
  } else {
    const resolved = resolveIcon(icon);
    if (!resolved) {
      inner = fallback;
    } else if (isImageIcon(resolved)) {
      inner = <img src={resolved} alt="" className="h-full w-full object-contain" />;
    } else {
      // Emoji / short text — inherits the token font size from the box.
      inner = <span className="leading-none">{resolved}</span>;
    }
  }
  return (
    <span className={cn('inline-flex items-center justify-center', className)} style={box}>
      {inner}
    </span>
  );
};

export default NodeIcon;
