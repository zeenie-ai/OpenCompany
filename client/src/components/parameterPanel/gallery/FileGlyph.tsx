import { createElement } from 'react';

import type { WorkspaceEntry } from '@/types/workspaceFiles';

import { glyphFor } from './fileIcons';

/**
 * Renders the icon for a workspace entry.
 *
 * A component rather than `const Glyph = glyphFor(entry)` at each call
 * site: binding a component to a capitalized local during render trips
 * `react-hooks/static-components`, because in the general case such an
 * identity churns and resets state. Resolving inside a module-scope
 * component keeps every call site clean and lint-honest.
 *
 * It lives in its own file so `fileIcons` stays a pure helper module —
 * a file that exports both components and helpers breaks Fast Refresh.
 */
export const FileGlyph = ({
  entry,
  className,
  strokeWidth,
}: {
  entry: WorkspaceEntry;
  className?: string;
  /** A large glyph reads better on a thinner line than lucide's default. */
  strokeWidth?: number;
}) => createElement(glyphFor(entry), { className, strokeWidth, 'aria-hidden': true });

export default FileGlyph;
