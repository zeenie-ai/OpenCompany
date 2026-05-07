/**
 * NodeIcon — single icon-rendering primitive.
 *
 * Resolves any backend-declared icon string (`lobehub:Claude`,
 * `asset:gmail`, `lucide:Battery`, emoji, URL, data URI) and renders
 * it inside a wrapper sized by Tailwind classes. The lucide / image
 * branches stretch to fill the wrapper; the emoji/text branch
 * inherits the wrapper's font size.
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
import { useTheme } from '../../contexts/ThemeContext';
import { THEMED_GLYPHS, ICON_KEYS, type IconKey } from './themedGlyphs';

export interface NodeIconProps {
  /** Backend-declared icon string. May be undefined while the spec
   *  cache hydrates — the component renders the fallback in that case. */
  icon: string | undefined | null;
  /** Wrapper class. Use Tailwind sizing tokens for the box (`h-6 w-6`)
   *  and a `text-X` class to size the emoji/text branch. */
  className?: string;
  /** Element rendered when the icon ref does not resolve. */
  fallback?: React.ReactNode;
}

export const NodeIcon: React.FC<NodeIconProps> = ({
  icon,
  className,
  fallback = null,
}) => {
  const { theme } = useTheme();
  let inner: React.ReactNode;

  // 1. Per-theme glyph override. Activates only when the icon prop is one
  //    of the conceptual `IconKey`s (`agent`, `trigger`, `tool`, …) AND
  //    the active theme declares an entry for it. Anything else (URLs,
  //    `asset:foo`, `lobehub:Brand`, `lucide:Bot`, emoji) skips this
  //    branch and falls through to the existing dispatch chain below.
  //    The SVG strings come from `themedGlyphs.ts` — author-trusted
  //    markup committed to the repo, never user input — so injecting
  //    via `dangerouslySetInnerHTML` is safe here. Do not extend this
  //    branch with values built from runtime input.
  if (icon && ICON_KEYS.has(icon as IconKey)) {
    const themedSvg = THEMED_GLYPHS[theme]?.[icon as IconKey];
    if (themedSvg) {
      // SAFE: `themedSvg` is an author-trusted constant from
      // `themedGlyphs.ts` — committed-to-repo markup, never user input.
      // The repo's ESLint config does not enable `react/no-danger`, so
      // no eslint-disable is needed; this comment documents the trust
      // boundary so future reviewers don't second-guess it.
      return (
        <span
          className={cn('inline-flex items-center justify-center', className)}
          dangerouslySetInnerHTML={{ __html: themedSvg }}
        />
      );
    }
    // No entry for this theme/key — fall through to the default chain so
    // the consumer's existing icon (lucide / asset / emoji) still renders.
  }

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
      // Emoji / short text — inherits font-size from the wrapper's
      // `text-X` class.
      inner = <span className="leading-none">{resolved}</span>;
    }
  }
  return (
    <span className={cn('inline-flex items-center justify-center', className)}>
      {inner}
    </span>
  );
};

export default NodeIcon;
