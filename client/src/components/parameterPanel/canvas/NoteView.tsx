/**
 * A Canvas note as a document (design handoff chat, "artifact panel"): its
 * versions (‹ v2/3 ›), Preview or source, Copy, and Download as .md, .html,
 * or .txt for other code. The board's item is the latest version; an
 * earlier one is read through `canvas_version` when the owner steps back to
 * it. A newer version arriving shows itself, unless the owner stepped back.
 * `focus` opens it at a version (a chat reply's artifact card names one).
 * Explicit HTML notes and a single fenced HTML document preview in the
 * same isolated sandbox as HTML files. Surrounding prose remains in Notes;
 * ordinary Markdown and code examples remain documents and code blocks.
 */

import { Check, ChevronLeft, ChevronRight, Copy, Download } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { useWebSocketActions } from '../../../contexts/WebSocketContext';
import type { CanvasFocus, CanvasItem, CanvasVersionResponse } from '../../../lib/canvasBoard';
import CanvasMarkdown from './CanvasMarkdown';

const COPIED_MS = 1600;
/** Only explicitly declared HTML or one fenced full HTML document previews. */
const DOCUMENT_LANGUAGES = new Set(['markdown', 'md']);
const HTML_LANGUAGES = new Set(['html', 'htm', 'text/html']);

function codeLanguage(language: string | null | undefined): string | null {
  const name = (language ?? '').trim().toLowerCase();
  return name && !DOCUMENT_LANGUAGES.has(name) ? name : null;
}

interface HtmlPreview {
  html: string;
  notes: string;
}

/** A complete HTML artifact inside Markdown, not an inline example. Track
 * every fence so HTML-looking text inside another code block stays code.
 * Only top-level fences (up to three spaces) participate; source offsets
 * keep the document and surrounding notes intact, including CRLF input. */
function htmlPreviewFor(content: string, language: string | null): HtmlPreview | null {
  if (language && HTML_LANGUAGES.has(language)) return { html: content, notes: '' };
  if (language) return null;

  let fence: { marker: string; length: number; html: boolean; start: number; body: number } | null = null;
  let candidate: HtmlPreview | null = null;
  let htmlFences = 0;
  let offset = 0;
  const lines = content.split('\n');
  for (let index = 0; index < lines.length; index += 1) {
    const rawLine = lines[index];
    const line = rawLine.replace(/\r$/, '');
    const nextOffset = offset + rawLine.length + (index < lines.length - 1 ? 1 : 0);
    if (fence) {
      const closing = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
      if (closing && closing[1][0] === fence.marker && closing[1].length >= fence.length) {
        if (fence.html) {
          const html = content.slice(fence.body, offset).replace(/\r?\n$/, '');
          const startsDocument = /^\s*(?:<!doctype\s+html(?:\s[^>]*)?>|<html(?:\s[^>]*)?>)/i.test(html);
          if (startsDocument && /<\/html>\s*$/i.test(html)) {
            candidate = {
              html,
              notes: (content.slice(0, fence.start) + content.slice(nextOffset)).trim(),
            };
          }
        }
        fence = null;
      }
    } else {
      const opening = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
      if (opening && !(opening[1][0] === '`' && opening[2].includes('`'))) {
        const html = HTML_LANGUAGES.has(opening[2].trim().toLowerCase());
        if (html) htmlFences += 1;
        fence = { marker: opening[1][0], length: opening[1].length, html, start: offset, body: nextOffset };
      }
    }
    offset = nextOffset;
  }
  return htmlFences === 1 && !fence?.html ? candidate : null;
}

/** Code, fenced so it previews as a code block whatever backticks it holds. */
function fenced(content: string, language: string): string {
  const longest = Math.max(0, ...(content.match(/`+/g) ?? []).map((run) => run.length));
  const fence = '`'.repeat(Math.max(3, longest + 1));
  return `${fence}${language}\n${content}\n${fence}`;
}

/** The download's name: the title, or "document", and the version. */
function noteFileName(title: string | null, version: number, extension: string): string {
  const base =
    (title ?? '')
      .replace(/[^\p{L}\p{N}\- ]+/gu, '')
      .trim()
      .replace(/\s+/g, '-')
      .slice(0, 60) || 'document';
  return `${base}${version > 1 ? `-v${version}` : ''}.${extension}`;
}

export default function NoteView({
  item,
  workflowId,
  nodeId,
  focus,
}: {
  item: CanvasItem;
  workflowId?: string | null;
  nodeId?: string | null;
  focus?: Pick<CanvasFocus, 'version' | 'nonce'> | null;
}) {
  const { sendRequest, isReady } = useWebSocketActions();
  const latest = item.version ?? 1;
  const [shown, setShown] = useState(latest);
  const [view, setView] = useState<'preview' | 'source'>('preview');
  const [copied, setCopied] = useState(false);

  // A newer version arrives: show it, unless the owner stepped back.
  const [seen, setSeen] = useState(latest);
  if (latest !== seen) {
    setSeen(latest);
    if (shown === seen) setShown(latest);
  }
  // A card asks for a version (each ask once).
  const [focusNonce, setFocusNonce] = useState<number | null>(null);
  if (focus && focus.nonce !== focusNonce) {
    setFocusNonce(focus.nonce);
    setShown(focus.version && focus.version >= 1 && focus.version <= latest ? focus.version : latest);
  }

  // Earlier versions are read from the board's node; without one, only the
  // latest shows.
  const canStep = latest > 1 && Boolean(workflowId && nodeId);
  const earlier = canStep && shown !== latest;
  const versionQuery = useQuery<CanvasVersionResponse>({
    queryKey: ['canvasVersion', workflowId ?? '', nodeId ?? '', item.id, shown],
    enabled: earlier && isReady,
    // A saved version never changes.
    staleTime: Infinity,
    queryFn: () =>
      sendRequest<CanvasVersionResponse>('canvas_version', {
        workflow_id: workflowId,
        node_id: nodeId,
        item_id: item.id,
        version: shown,
      }),
  });
  const failed = earlier && (versionQuery.isError || versionQuery.data?.success === false);
  const shownItem = earlier ? versionQuery.data?.item : item;
  const content = shownItem?.content ?? '';
  const code = codeLanguage(shownItem?.language);
  const htmlPreview = useMemo(() => htmlPreviewFor(content, code), [content, code]);
  const isHtml = htmlPreview !== null;
  const extension = isHtml ? 'html' : code ? 'txt' : 'md';
  const mimeType = isHtml ? 'text/html' : code ? 'text/plain' : 'text/markdown';
  const number = earlier ? shown : latest;

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const copy = () => {
    void navigator.clipboard?.writeText(content).then(
      () => setCopied(true),
      () => undefined,
    );
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([htmlPreview?.html ?? content], { type: `${mimeType};charset=utf-8` }));
    const link = document.createElement('a');
    link.href = url;
    link.download = noteFileName(shownItem?.title ?? null, number, extension);
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  const ready = !(earlier && (versionQuery.isPending || failed));

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
        {canStep && (
          <span role="group" aria-label="Versions" className="flex items-center font-mono text-xs text-fg-muted">
            <Button
              variant="quiet"
              size="icon-sm"
              onClick={() => setShown((value) => Math.max(1, value - 1))}
              disabled={shown <= 1}
              aria-label="Previous version"
            >
              <ChevronLeft className="size-3.5" />
            </Button>
            <span className="tabular-nums">
              v{shown}/{latest}
            </span>
            <Button
              variant="quiet"
              size="icon-sm"
              onClick={() => setShown((value) => Math.min(latest, value + 1))}
              disabled={shown >= latest}
              aria-label="Next version"
            >
              <ChevronRight className="size-3.5" />
            </Button>
          </span>
        )}
        <ToggleGroup
          type="single"
          variant="segmented"
          size="sm"
          value={view}
          onValueChange={(value) => {
            if (value === 'preview' || value === 'source') setView(value);
          }}
          aria-label="Show as"
          className="ml-auto"
        >
          <ToggleGroupItem value="preview">Preview</ToggleGroupItem>
          <ToggleGroupItem value="source">{code || isHtml ? 'Source' : 'Markdown'}</ToggleGroupItem>
        </ToggleGroup>
        <Button
          variant="quiet"
          size="icon-sm"
          onClick={copy}
          disabled={!ready}
          aria-label={copied ? 'Copied' : 'Copy'}
          title={copied ? 'Copied' : 'Copy'}
        >
          {copied ? <Check className="size-3.5 text-action-run-ink" /> : <Copy className="size-3.5" />}
        </Button>
        <Button variant="quiet" size="icon-sm" onClick={download} disabled={!ready} aria-label="Download" title={`Download as a .${extension} file`}>
          <Download className="size-3.5" />
        </Button>
      </div>
      {failed ? (
        <p className="m-0 text-xs text-fg-muted">Couldn’t load version {shown}.</p>
      ) : !ready ? (
        <p className="m-0 text-xs text-fg-muted">Loading version {shown}…</p>
      ) : view === 'source' ? (
        <pre className="m-0 min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded bg-bg-elevated p-3 font-mono text-xs text-fg-default">
          {content}
        </pre>
      ) : htmlPreview ? (
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <iframe
            key={`${item.id}:${number}`}
            srcDoc={htmlPreview.html}
            title={`${shownItem?.title || 'HTML document'} preview`}
            sandbox="allow-scripts"
            className="min-h-0 w-full flex-1 rounded border border-border-default bg-bg-panel"
          />
          {htmlPreview.notes && (
            <details className="shrink-0 rounded border border-border-default px-3 py-2 text-xs text-fg-muted">
              <summary className="cursor-pointer">Notes</summary>
              <div className="prose prose-sm mt-2 max-h-40 max-w-none overflow-auto dark:prose-invert">
                <CanvasMarkdown workflowId={workflowId}>{htmlPreview.notes}</CanvasMarkdown>
              </div>
            </details>
          )}
        </div>
      ) : (
        <div className="prose prose-sm min-h-0 max-w-none flex-1 overflow-auto dark:prose-invert">
          {/* A note names workspace files from the workspace's root. */}
          <CanvasMarkdown workflowId={workflowId}>{code ? fenced(content, code) : content}</CanvasMarkdown>
        </div>
      )}
    </div>
  );
}
