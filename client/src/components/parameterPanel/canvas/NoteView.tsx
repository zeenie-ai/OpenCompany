/**
 * A Canvas note as a document (design handoff chat, "artifact panel"): its
 * versions (‹ v2/3 ›), Preview or the Markdown source, Copy, and Download
 * as a .md file (.txt for code). The board's item is the latest version; an
 * earlier one is read through `canvas_version` when the owner steps back to
 * it. A newer version arriving shows itself, unless the owner stepped back.
 * `focus` opens it at a version (a chat reply's artifact card names one). A
 * note with a language other than Markdown is code, previewed as a code
 * block (the server names it the same way on a reply's card).
 */

import { Check, ChevronLeft, ChevronRight, Copy, Download } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Button } from '@/components/ui/button';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { useWebSocketActions } from '../../../contexts/WebSocketContext';
import type { CanvasFocus, CanvasItem, CanvasVersionResponse } from '../../../lib/canvasBoard';
import CanvasMarkdown from './CanvasMarkdown';

const COPIED_MS = 1600;
/** Languages a note is a document in; any other makes it code. */
const DOCUMENT_LANGUAGES = new Set(['markdown', 'md']);

function codeLanguage(language: string | null | undefined): string | null {
  const name = (language ?? '').trim().toLowerCase();
  return name && !DOCUMENT_LANGUAGES.has(name) ? name : null;
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
  const content = earlier ? (versionQuery.data?.item?.content ?? '') : (item.content ?? '');
  const code = codeLanguage(earlier ? versionQuery.data?.item?.language : item.language);
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
    const url = URL.createObjectURL(new Blob([content], { type: `${code ? 'text/plain' : 'text/markdown'};charset=utf-8` }));
    const link = document.createElement('a');
    link.href = url;
    link.download = noteFileName(item.title, number, code ? 'txt' : 'md');
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
              variant="ghost"
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
              variant="ghost"
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
          <ToggleGroupItem value="source">Markdown</ToggleGroupItem>
        </ToggleGroup>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={copy}
          disabled={!ready}
          aria-label={copied ? 'Copied' : 'Copy'}
          title={copied ? 'Copied' : 'Copy'}
        >
          {copied ? <Check className="size-3.5 text-action-run-ink" /> : <Copy className="size-3.5" />}
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={download} disabled={!ready} aria-label="Download" title={`Download as a .${code ? 'txt' : 'md'} file`}>
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
      ) : (
        <div className="prose prose-sm min-h-0 max-w-none flex-1 overflow-auto dark:prose-invert">
          {/* A note names workspace files from the workspace's root. */}
          <CanvasMarkdown workflowId={workflowId}>{code ? fenced(content, code) : content}</CanvasMarkdown>
        </div>
      )}
    </div>
  );
}
