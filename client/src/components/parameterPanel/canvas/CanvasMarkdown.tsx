/**
 * Markdown on the Canvas board: a note, or a .md file in the workspace.
 *
 * Its images and links name workspace files by path, read against the
 * workspace (lib/workspaceLinks.ts), as a chat reply's are. Every image then
 * goes through the chat's SafeImage: an image from the workspace loads, and
 * one from anywhere else waits for the owner behind a button naming its
 * host, since a note is model output just as a reply is, and an image URL
 * can carry data away the moment it loads. A link opens in a new tab, so
 * following one never leaves the app.
 */

import ReactMarkdown, { type Components } from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';

import { SafeImage } from '@/features/chat';
import { workspaceUrlTransform } from '@/lib/workspaceLinks';

const REMARK_PLUGINS = [remarkGfm, remarkBreaks];

const COMPONENTS: Components = {
  img: SafeImage,
  a: ({ node: _node, href, ...props }) =>
    href?.startsWith('#') ? (
      <a {...props} href={href} />
    ) : (
      <a {...props} href={href} target="_blank" rel="noopener noreferrer" />
    ),
};

interface Props {
  children: string;
  /** The workflow whose workspace the paths name; without one they stay as written. */
  workflowId?: string | null;
  /** The folder a relative path starts from: '' for the workspace's root (a
   *  note), a .md file's own folder for the file. */
  baseDir?: string;
}

export default function CanvasMarkdown({ children, workflowId, baseDir = '' }: Props) {
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      components={COMPONENTS}
      urlTransform={workspaceUrlTransform(workflowId, baseDir)}
    >
      {children}
    </ReactMarkdown>
  );
}
