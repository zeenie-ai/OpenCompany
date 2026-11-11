/**
 * The three iframe surfaces of the Canvas board. Zero-precedent territory —
 * these sandbox attributes are the security decisions:
 *
 * - External URL: `sandbox="allow-scripts allow-forms"` and NOTHING else.
 *   No allow-same-origin (opaque origin), no allow-top-navigation (a framed
 *   page must never navigate the app away), no allow-popups (the permanent
 *   Open-in-new-tab button is the escape hatch). referrerPolicy keeps
 *   workflow URLs out of third-party logs.
 * - Workspace HTML: fetched as text and rendered via `srcDoc` with
 *   `sandbox="allow-scripts"` — never `allow-same-origin`, so a stored-XSS
 *   payload in a workspace file executes with no origin, no cookies, no
 *   app storage. Workspace HTML never enters the app DOM (also why no
 *   sanitizer dependency is needed). This respects the backend's
 *   NEVER_INLINE rule rather than working around it.
 * - PDF: plain same-origin iframe at the file URL — the browser's built-in
 *   viewer; sandboxing breaks it in several engines. Served inline by the
 *   route since `application/pdf` joined INLINE_EXACT.
 */

import React from 'react';
import { AlertCircle, Download, ExternalLink, RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { buildApiUrl } from '../../../config/api';
import { useWorkspaceTextQuery } from '../../../hooks/useWorkspaceText';
import type { WorkspaceFileRef } from '../../../types/workspaceFiles';

/** A neutral outline (theme_system.md, "Colour marks state or role"). */
const NEUTRAL = 'border-border-default text-fg-default';

const SurfaceHeader: React.FC<{
  label: string;
  href: string;
  hint?: string;
}> = ({ label, href, hint }) => (
  <div className="mb-2 flex shrink-0 items-center gap-2">
    <span className="min-w-0 flex-1 truncate font-mono text-xs text-fg-muted" title={label}>
      {label}
    </span>
    {hint && <span className="hidden shrink-0 text-[10px] text-fg-faint sm:inline">{hint}</span>}
    <Button asChild variant="quiet" size="sm" className={NEUTRAL}>
      <a href={href} target="_blank" rel="noopener noreferrer">
        <ExternalLink className="h-3.5 w-3.5" /> Open in new tab
      </a>
    </Button>
  </div>
);

export const ExternalSiteView: React.FC<{ url: string; title?: string | null }> = ({
  url,
  title,
}) => (
  <div className="flex min-h-0 flex-1 flex-col">
    <SurfaceHeader
      label={url}
      href={url}
      hint="Some sites refuse embedding"
    />
    <iframe
      src={url}
      title={title || url}
      sandbox="allow-scripts allow-forms"
      referrerPolicy="no-referrer"
      className="min-h-0 w-full flex-1 rounded border border-border-default bg-bg-panel"
    />
  </div>
);

export const WorkspaceHtmlView: React.FC<{ refFile: WorkspaceFileRef }> = ({ refFile }) => {
  const query = useWorkspaceTextQuery(refFile);
  const href = refFile.url ? buildApiUrl(refFile.url) : null;

  if (!href) {
    return (
      <p className="py-8 text-center text-xs text-fg-muted">
        Save and run this workflow to give its files a stable address.
      </p>
    );
  }
  if (query.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center py-10">
        <RefreshCw className="h-4 w-4 animate-spin text-fg-muted" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <p className="flex items-center justify-center gap-2 py-8 text-center text-xs text-fg-muted">
        <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
        Could not load this file. It may have been removed, or the session expired.
      </p>
    );
  }
  if (query.data?.truncated) {
    // A half-rendered page misleads (text gets a banner, HTML gets a refusal).
    return (
      <div className="flex flex-col items-center gap-3 py-10 text-center">
        <p className="text-xs text-fg-muted">
          This page is larger than the 512 KB render cap — download it instead.
        </p>
        <Button asChild variant="quiet" size="sm" className={NEUTRAL}>
          <a href={href} download={refFile.filename} target="_blank" rel="noreferrer">
            <Download className="h-4 w-4" /> Download
          </a>
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <SurfaceHeader label={refFile.filename} href={href} />
      <iframe
        srcDoc={query.data?.text ?? ''}
        title={refFile.filename}
        sandbox="allow-scripts"
        className="min-h-0 w-full flex-1 rounded border border-border-default bg-bg-panel"
      />
    </div>
  );
};

export const PdfView: React.FC<{ refFile: WorkspaceFileRef }> = ({ refFile }) => {
  const href = refFile.url ? buildApiUrl(refFile.url) : null;
  if (!href) {
    return (
      <p className="py-8 text-center text-xs text-fg-muted">
        Save and run this workflow to give its files a stable address.
      </p>
    );
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <SurfaceHeader label={refFile.filename} href={href} />
      <iframe
        src={href}
        title={refFile.filename}
        className="min-h-0 w-full flex-1 rounded border border-border-default bg-bg-panel"
      />
    </div>
  );
};
