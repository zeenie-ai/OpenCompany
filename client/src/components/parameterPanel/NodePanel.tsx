/**
 * The frame of a node's full-height panel (Gallery, Canvas, Process Manager,
 * Team Monitor, Task Manager): a head with the panel's name, a line about it
 * and its buttons, then whatever the panel shows. One frame, so these panels
 * read as one family (design-system/reference-mockup, panels 11 to 14).
 *
 * The panel's surface is `bg-bg-panel`; cards on it are `bg-bg-elevated`.
 */

import type { ReactNode } from 'react';

interface NodePanelProps {
  title: string;
  /** A line or two under the title. */
  description?: ReactNode;
  /** The panel's buttons, at the head's right. */
  actions?: ReactNode;
  /** A second row in the head: counts or filters. */
  summary?: ReactNode;
  /** The body. It lays itself out: the frame only stacks it under the head. */
  children: ReactNode;
}

export function NodePanel({ title, description, actions, summary, children }: NodePanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-1 flex-col bg-bg-panel">
      <header className="shrink-0 border-b border-border-default px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
          <div className="min-w-0 flex-1 basis-64">
            <h2 className="font-display text-md font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
              {title}
            </h2>
            {description && <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
        {summary && <div className="mt-4">{summary}</div>}
      </header>
      {children}
    </div>
  );
}

export default NodePanel;
