/**
 * An answer's text as markdown: paragraphs, lists, quotes, tables in a frame,
 * and code blocks in a frame with their language, Copy and line numbers, in
 * the theme's syntax colours (`.chat-markdown .token.*`; the frames are
 * `.chat-code` and `.chat-table` in index.css). Its own chunk: the thread
 * loads it lazily, so the markdown stack stays out of the first load. Links
 * open in a new tab, and images from outside the workspace wait for the owner
 * (CHAT_MARKDOWN_COMPONENTS).
 *
 * While the answer streams (`streaming`), it is rendered block by block
 * (markdown/blocks.ts): a finished block renders once, only the growing last
 * one again with each delta, and a caret follows the text, though not a code
 * block or a table (`.chat-markdown[data-streaming]` in index.css).
 */

import { Check, Copy } from 'lucide-react';
import Prism from 'prismjs';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-markdown';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-yaml';
import { memo, useContext, useMemo } from 'react';
import ReactMarkdown, { type Components, type ExtraProps } from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useCopied } from '../turns/useCopied';
import { markdownBlocks } from './blocks';
import { CitationContext } from './citationContext';
import { linkCitations } from './citations';
import { CHAT_MARKDOWN_COMPONENTS } from './components';

const REMARK_PLUGINS = [remarkGfm, remarkBreaks];

/** The names a fence uses for a language Prism knows by another. */
const LANGUAGE_ALIASES: Record<string, string> = {
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  py: 'python',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  yml: 'yaml',
  md: 'markdown',
  html: 'markup',
  xml: 'markup',
  svg: 'markup',
};

/** The language a fence names (`language-x` on its code element). */
function fenceLanguage(className: string | undefined): string | undefined {
  return /(?:^|\s)language-([\w-]+)/.exec(className ?? '')?.[1]?.toLowerCase();
}

/** A fenced block in a language Prism knows gets its colours; inline code
 *  (which never has a language class) and anything else stays as written.
 *  Prism escapes the text it tokenizes, so its markup is safe to insert. */
const CodeBlock: Components['code'] = ({ node: _node, className, children, ...props }) => {
  const fence = fenceLanguage(className);
  const language = fence ? (LANGUAGE_ALIASES[fence] ?? fence) : undefined;
  const grammar = language ? Prism.languages[language] : undefined;
  if (!language || !grammar || typeof children !== 'string') {
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  }
  const html = Prism.highlight(children.replace(/\n$/, ''), grammar, language);
  return <code className={className} {...props} dangerouslySetInnerHTML={{ __html: html }} />;
};

/** The fence's language and the code as written, from the code element in a
 *  block's `pre` (the parser ends that text with a newline of its own). */
function codeOf(node: ExtraProps['node']): { language: string | undefined; text: string } {
  const code = node?.children[0];
  if (code?.type !== 'element') return { language: undefined, text: '' };
  const text = code.children.map((child) => (child.type === 'text' ? child.value : '')).join('');
  return { language: fenceLanguage([code.properties.className].flat().join(' ')), text: text.replace(/\n$/, '') };
}

/** A code block in its frame (design handoff chat, "code"): the fence's
 *  language and Copy above, a line number beside each line. `children` is
 *  the code element as CodeBlock draws it. */
const CodeFrame: Components['pre'] = ({ node, children, ...props }) => {
  const [copied, copy] = useCopied();
  const { language, text } = codeOf(node);
  const lines = text.split('\n').length;
  return (
    <figure className="chat-code">
      <figcaption className="flex h-9 items-center gap-2 border-b border-border-default bg-bg-panel pr-1.5 pl-3.5">
        <span className="font-mono text-xs font-medium text-fg-muted">{language ?? 'text'}</span>
        <Button
          variant="quiet"
          size="xs"
          onClick={() => copy(text)}
          aria-label={copied ? 'Copied' : 'Copy code'}
          className="ml-auto h-6.5 gap-1.5 px-2 text-xs font-medium"
        >
          {copied ? (
            <Check aria-hidden className="size-3.25 text-action-run-ink" />
          ) : (
            <Copy aria-hidden className="size-3.25" />
          )}
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </figcaption>
      <div className="flex overflow-x-auto py-3 font-mono leading-relaxed">
        <span aria-hidden className="w-10 shrink-0 pr-3.5 text-right text-fg-faint select-none">
          {Array.from({ length: lines }, (_, index) => (
            <span key={index} className="block">
              {index + 1}
            </span>
          ))}
        </span>
        {/* Spacing from `.chat-markdown .chat-code pre`, which outranks utility
            classes; no min-w-0, so a long line scrolls with its padding after it. */}
        <pre {...props} className="flex-1">
          {children}
        </pre>
      </div>
    </figure>
  );
};

/** A table in its frame, which scrolls sideways when the table is wider. */
const Table: Components['table'] = ({ node: _node, ...props }) => (
  <div className="chat-table">
    <table {...props} />
  </div>
);

const COMPONENTS: Components = { ...CHAT_MARKDOWN_COMPONENTS, code: CodeBlock, pre: CodeFrame, table: Table };

const Block = memo(function Block({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={COMPONENTS}>
      {text}
    </ReactMarkdown>
  );
});

export default function ReplyMarkdown({ text, streaming = false, className }: { text: string; streaming?: boolean; className?: string }) {
  // Each [n] of a source the reply has becomes a citation chip.
  const { sources } = useContext(CitationContext);
  const linked = useMemo(() => linkCitations(text, new Set(sources.keys())), [text, sources]);
  const blocks = useMemo(() => (streaming ? markdownBlocks(linked) : [linked]), [linked, streaming]);
  return (
    <div className={cn('chat-markdown min-w-0', className)} data-streaming={streaming || undefined}>
      {blocks.map((block, index) => (
        <Block key={index} text={block} />
      ))}
    </div>
  );
}
