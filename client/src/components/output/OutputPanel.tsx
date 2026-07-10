/**
 * OutputPanel — execution output display.
 *
 * shadcn primitives (Badge, Button, Collapsible) + ReactMarkdown
 * + @uiw/react-json-view. Backend owns display logic via `_uiHints` (future).
 */

import { ChevronDown, X, Copy } from 'lucide-react';
import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import JsonView from '@uiw/react-json-view';
import { Node } from 'reactflow';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { ExecutionResult } from '@/services/executionService';
import { useNodeSpec } from '@/lib/nodeSpec';
import { copyToClipboard, tryParseJson } from '@/utils/formatters';
import { cn } from '@/lib/utils';

/** Extract output data from ExecutionResult. */
const getData = (r: ExecutionResult) =>
  r.outputs ?? r.data ?? r.nodeData?.[0]?.[0]?.json ?? { success: r.success };

/** Unwrap nested { result: {...} } from backend responses. */
const unwrap = (d: any) =>
  d?.result && typeof d.result === 'object' && !Array.isArray(d.result) ? d.result : d;

/** Convert escaped newlines to real ones. */
const fmt = (s: string) => s.replace(/\\n/g, '\n').replace(/\\t/g, '\t');

/** Terminal-style surface for CLI stdout — the same --code-* tokens the
 * JSON viewer uses, so all 12 themes paint it in their own palette. */
const TERMINAL_STYLE = {
  background: 'var(--code-bg)',
  color: 'var(--code-text)',
  borderColor: 'var(--code-border)',
} as React.CSSProperties;

/** Map metadata field names to badge variants. */
const TAG_VARIANT: Record<string, 'secondary' | 'info' | 'success'> = {
  model: 'secondary',
  provider: 'info',
  agent_type: 'success',
};

/** @uiw/react-json-view palette driven entirely by the active theme's
 * --code-* tokens — no isDarkMode branch, so all 12 themes paint Raw JSON in
 * their own syntax colors. The CSS vars re-resolve on theme switch (the
 * data-theme attribute changes the --code-* values; no React re-render). */
const CODE_JSON_THEME = {
  '--w-rjv-background-color': 'var(--code-bg)',
  '--w-rjv-color': 'var(--code-text)',
  '--w-rjv-key-string': 'var(--code-tag)',
  '--w-rjv-line-color': 'var(--code-border)',
  '--w-rjv-arrow-color': 'var(--code-gutter-fg)',
  '--w-rjv-info-color': 'var(--code-comment)',
  '--w-rjv-ellipsis-color': 'var(--code-comment)',
  '--w-rjv-brackets-color': 'var(--code-punctuation)',
  '--w-rjv-curlybraces-color': 'var(--code-punctuation)',
  '--w-rjv-colon-color': 'var(--code-punctuation)',
  '--w-rjv-quotes-color': 'var(--code-string)',
  '--w-rjv-quotes-string-color': 'var(--code-string)',
  '--w-rjv-type-string-color': 'var(--code-string)',
  '--w-rjv-type-int-color': 'var(--code-number)',
  '--w-rjv-type-float-color': 'var(--code-number)',
  '--w-rjv-type-bigint-color': 'var(--code-number)',
  '--w-rjv-type-boolean-color': 'var(--code-boolean)',
  '--w-rjv-type-null-color': 'var(--code-number)',
  '--w-rjv-type-nan-color': 'var(--code-number)',
  '--w-rjv-type-undefined-color': 'var(--code-comment)',
  '--w-rjv-type-date-color': 'var(--code-string)',
} as React.CSSProperties;

interface Props {
  results: ExecutionResult[];
  onClear?: () => void;
  selectedNode?: Node | null;
}

interface SectionProps {
  label: React.ReactNode;
  defaultOpen?: boolean;
  action?: React.ReactNode;
  children: React.ReactNode;
}

function Section({ label, defaultOpen = false, action, children }: SectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="border-b border-border last:border-0">
      <div className="flex items-center gap-2">
        <CollapsibleTrigger className="flex flex-1 items-center gap-2 py-2 text-sm font-medium text-foreground hover:text-primary">
          <ChevronDown
            className={cn('h-4 w-4 transition-transform', open && 'rotate-0', !open && '-rotate-90')}
          />
          <span className="flex-1 text-left">{label}</span>
        </CollapsibleTrigger>
        {action && <div className="shrink-0">{action}</div>}
      </div>
      <CollapsibleContent className="pb-3 pl-6">{children}</CollapsibleContent>
    </Collapsible>
  );
}

export default function OutputPanel({ results, onClear, selectedNode }: Props) {
  const filtered = selectedNode ? results.filter(r => r.nodeId === selectedNode.id) : results;
  const latest = filtered[0];
  // Backend owns display logic: CLI-wrapper plugins declare
  // uiHints.outputMode = "terminal" on their NodeSpec.
  const spec = useNodeSpec(selectedNode?.type);
  const isTerminal = spec?.uiHints?.outputMode === 'terminal';

  if (!latest) {
    return (
      <div className="flex h-full w-full items-center justify-center text-sm text-muted-foreground">
        No output yet. Run a node to see results.
      </div>
    );
  }

  const raw = getData(latest);
  const data = unwrap(raw);
  // `result` is the codebase's canonical payload key (same convention
  // `unwrap` peels) — CLI nodes put server-side-parsed JSON there.
  // Arrays survive `unwrap` un-peeled, so surface them here.
  const parsedResult = data?.result !== null && typeof data?.result === 'object' ? data.result : undefined;
  const response =
    data?.response ?? data?.output ?? data?.text ?? data?.content ?? parsedResult ?? data?.stdout;
  // Terminal nodes sometimes emit JSON on stdout anyway — a tree view
  // beats a wall of braces (stdlib JSON.parse via shared formatter).
  const stdoutJson = isTerminal && typeof response === 'string' ? tryParseJson(response) : null;
  const thinking = data?.thinking;
  const metaTags = ['model', 'provider', 'agent_type'].filter(k => data?.[k]);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-card">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border bg-card px-4 py-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
          <span className="mr-1 text-sm font-semibold">Output</span>
          <Badge variant={latest.success ? 'success' : 'destructive'}>
            {latest.success ? 'Success' : 'Error'}
          </Badge>
          {latest.executionTime > 0 && (
            <Badge variant="outline">{(latest.executionTime / 1000).toFixed(1)}s</Badge>
          )}
          {metaTags.map(k => (
            <Badge key={k} variant={TAG_VARIANT[k] ?? 'secondary'}>
              {String(data[k]).replace(/_/g, ' ')}
            </Badge>
          ))}
        </div>
        {onClear && (
          <Button variant="ghost" size="xs" onClick={onClear} className="shrink-0 text-destructive">
            <X /> Clear
          </Button>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4">
        {latest.error && (
          <pre className="mb-3 overflow-auto rounded-md border border-destructive/40 border-l-4 border-l-destructive bg-destructive/5 p-3 font-mono text-sm whitespace-pre-wrap break-words text-destructive">
            {latest.error}
          </pre>
        )}

        <div className="space-y-0">
          {response && (
            <Section label="Response" defaultOpen>
              {typeof response !== 'string' ? (
                // Object/array response — themed JSON viewer (same --code-*
                // palette as Raw JSON), not stringified-into-markdown.
                <JsonView
                  value={response}
                  collapsed={2}
                  displayDataTypes={false}
                  style={CODE_JSON_THEME}
                />
              ) : stdoutJson ? (
                // Terminal node whose stdout is itself JSON — tree view.
                <JsonView
                  value={stdoutJson}
                  collapsed={2}
                  displayDataTypes={false}
                  style={CODE_JSON_THEME}
                />
              ) : isTerminal ? (
                // CLI text (uiHints.outputMode === 'terminal'): preformatted,
                // never markdown — `#` would become headings and indentation
                // would collapse (gh/vercel tables, clone progress).
                <pre
                  className="overflow-auto rounded-md border p-3 font-mono text-sm whitespace-pre-wrap break-words"
                  style={TERMINAL_STYLE}
                >
                  {fmt(response)}
                </pre>
              ) : (
                // Markdown text (LLM response). No whitespace-pre-wrap: it
                // double-counts newlines against remarkBreaks (each <br>
                // carries a trailing \n that pre-wrap renders as a 2nd break).
                <div className="prose prose-sm max-w-none dark:prose-invert">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                    {fmt(response)}
                  </ReactMarkdown>
                </div>
              )}
            </Section>
          )}

          {thinking && (
            <Section label="Thinking">
              <div className="prose prose-sm max-h-[300px] max-w-none overflow-auto dark:prose-invert">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                  {fmt(thinking)}
                </ReactMarkdown>
              </div>
            </Section>
          )}

          <Section
            label="Raw JSON"
            action={
              <button
                type="button"
                onClick={() => copyToClipboard(raw, 'Copied!')}
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                <Copy className="h-3 w-3" />
                Copy
              </button>
            }
          >
            <JsonView
              value={raw}
              collapsed={2}
              displayDataTypes={false}
              style={CODE_JSON_THEME}
            />
          </Section>
        </div>
      </div>
    </div>
  );
}
