/**
 * Fetched-text renderers for Canvas file items: markdown, JSON, code, text.
 *
 * Bodies come over the existing workspace route via useWorkspaceTextQuery
 * (capped + truncation-flagged). All code/text paints on the per-theme
 * `--code-*` surface tokens; markdown uses the same prose classes as
 * OutputPanel.
 */

import React, { useMemo } from 'react';
import JsonView from '@uiw/react-json-view';
import { AlertCircle, AlertTriangle, RefreshCw } from 'lucide-react';
import Prism from 'prismjs';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-markdown';

import type { WorkspaceFileRef } from '../../../types/workspaceFiles';
import { useWorkspaceTextQuery } from '../../../hooks/useWorkspaceText';
import CanvasMarkdown from './CanvasMarkdown';
import { HIGHLIGHT_MAX_CHARS, parentDirOf } from './canvasKinds';

interface Props {
  refFile: WorkspaceFileRef;
  verdict: 'markdown' | 'json' | 'code' | 'text';
  /** Prism grammar name, when one is known. */
  language: string | null;
}

/** A notice: the warning's mark beside neutral words, as the Gallery's. */
const TruncationBanner: React.FC<{ sizeBytes?: number }> = ({ sizeBytes }) => (
  <p className="mb-2 flex shrink-0 items-center gap-1.5 rounded-md border border-border-default bg-bg-panel px-2 py-1 text-xs text-fg-muted">
    <AlertTriangle aria-hidden className="size-3.5 shrink-0 text-warning" />
    Showing the first 512 KB{sizeBytes ? ` of ${Math.round(sizeBytes / 1024)} KB` : ''}. Use Download for the
    full file.
  </p>
);

const CodeSurface: React.FC<{ text: string; language: string | null }> = ({
  text,
  language,
}) => {
  const grammar = language ? Prism.languages[language] : undefined;
  const highlighted = useMemo(() => {
    if (!grammar || text.length > HIGHLIGHT_MAX_CHARS) return null;
    try {
      // Prism escapes entities while tokenizing — same usage as the
      // ConsolePanel JSON highlighter.
      return Prism.highlight(text, grammar, language as string);
    } catch {
      return null;
    }
  }, [grammar, language, text]);

  return (
    <pre className="min-h-0 flex-1 overflow-auto rounded border border-code-border bg-code-bg p-3 font-mono text-xs leading-relaxed text-code-text">
      {highlighted !== null ? (
        <code dangerouslySetInnerHTML={{ __html: highlighted }} />
      ) : (
        <code>{text}</code>
      )}
    </pre>
  );
};

const TextFileView: React.FC<Props> = ({ refFile, verdict, language }) => {
  const query = useWorkspaceTextQuery(refFile);

  if (!refFile.url) {
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

  const { text, truncated } = query.data ?? { text: '', truncated: false };

  if (verdict === 'markdown') {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {truncated && <TruncationBanner sizeBytes={refFile.size_bytes} />}
        <div className="prose prose-sm min-h-0 max-w-none flex-1 overflow-auto dark:prose-invert">
          {/* A .md file names other files from its own folder. */}
          <CanvasMarkdown workflowId={refFile.workflow_id} baseDir={parentDirOf(refFile.path)}>
            {text}
          </CanvasMarkdown>
        </div>
      </div>
    );
  }

  if (verdict === 'json') {
    let parsed: unknown;
    let parseFailed = false;
    try {
      parsed = JSON.parse(text);
    } catch {
      parseFailed = true;
    }
    if (!parseFailed && !truncated) {
      return (
        <div className="min-h-0 flex-1 overflow-auto rounded border border-code-border bg-code-bg p-2">
          <JsonView value={parsed as object} collapsed={2} displayDataTypes={false} />
        </div>
      );
    }
    // Truncated JSON never parses honestly; degrade to the code surface.
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {truncated && <TruncationBanner sizeBytes={refFile.size_bytes} />}
        <CodeSurface text={text} language="json" />
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {truncated && <TruncationBanner sizeBytes={refFile.size_bytes} />}
      <CodeSurface text={text} language={verdict === 'code' ? language : null} />
    </div>
  );
};

export default TextFileView;
