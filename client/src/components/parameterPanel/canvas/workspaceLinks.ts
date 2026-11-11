/**
 * The workspace files a Canvas document's markdown names. An agent writes a
 * report's images and links by their workspace path
 * (`![Revenue](charts/revenue.png)`), and a browser would read such a path
 * against the app's own address, where no file is. So a relative path is
 * read from the document's folder (the workspace's root for a note), a path
 * starting with `/` from the workspace's root, and either becomes the
 * workspace file route, which checks it stays inside the workflow's
 * workspace. Anything else (a URL with a scheme or a host, an app route, a
 * fragment) is left as written.
 */

import { buildApiUrl } from '@/config/api';

const SCHEME = /^[a-z][a-z\d+.-]*:/i;

/** A file's address on the workspace file route, encoded as the server
 *  encodes a FileRef's url (services/media/workspace.py). */
export function workspaceFileUrl(workflowId: string, path: string): string {
  const encoded = path.split('/').map(encodeURIComponent).join('/');
  return buildApiUrl(`/api/workspace/${encodeURIComponent(workflowId)}/files/${encoded}`);
}

/** The workspace path a reference names, read from `baseDir`; null when it
 *  names none, or climbs out of the workspace. */
export function workspacePathOf(reference: string, baseDir = ''): string | null {
  if (!reference || reference.startsWith('//') || reference.startsWith('/api/') || SCHEME.test(reference)) {
    return null;
  }
  let decoded = reference;
  try {
    // Markdown hands a destination over percent-encoded; it is encoded again
    // whole when it becomes a URL.
    decoded = decodeURIComponent(reference);
  } catch {
    // A stray `%` is part of the name.
  }
  const parts = /^[\\/]/.test(decoded) ? [] : baseDir.split('/').filter(Boolean);
  for (const segment of decoded.split(/[\\/]/)) {
    if (!segment || segment === '.') continue;
    if (segment === '..') {
      if (parts.length === 0) return null;
      parts.pop();
    } else {
      parts.push(segment);
    }
  }
  return parts.length > 0 ? parts.join('/') : null;
}

/** A markdown image's or link's address: a workspace path as the workspace
 *  file route (its query or fragment kept), anything else unchanged. */
export function resolveWorkspaceReference(
  reference: string,
  workflowId: string | null | undefined,
  baseDir = '',
): string {
  if (!workflowId) return reference;
  const cut = reference.search(/[?#]/);
  const path = workspacePathOf(cut < 0 ? reference : reference.slice(0, cut), baseDir);
  if (path === null) return reference;
  return workspaceFileUrl(workflowId, path) + (cut < 0 ? '' : reference.slice(cut));
}
