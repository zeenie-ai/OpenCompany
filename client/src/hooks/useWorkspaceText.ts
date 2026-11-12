/**
 * Capped client-side fetch of a workspace file's TEXT body.
 *
 * The first client-side file-content fetch in the repo. The workspace route
 * serves non-media files with `Content-Disposition: attachment`, which blocks
 * navigation/embedding but NOT `fetch()` reading the body — so markdown /
 * code / JSON / HTML rendering needs no backend change.
 *
 * Bounded by construction: the body is read through a streamed reader and
 * cancelled at the cap, so a 100 MB log never lands in memory. jsdom (tests)
 * lacks `res.body` — falls back to `res.text()` + slice.
 */

import { useQuery } from '@tanstack/react-query';

import { buildApiUrl } from '../config/api';
import { STALE_TIME } from '../lib/queryConfig';
import type { WorkspaceFileRef } from '../types/workspaceFiles';

export const TEXT_FETCH_CAP_BYTES = 512 * 1024;
/** Standalone HTML can embed images and 3D models; keep a separate bounded budget. */
export const HTML_FETCH_CAP_BYTES = 8 * 1024 * 1024;

export interface FetchedText {
  text: string;
  truncated: boolean;
}

export async function fetchWorkspaceText(
  url: string,
  capBytes: number = TEXT_FETCH_CAP_BYTES,
): Promise<FetchedText> {
  const response = await fetch(buildApiUrl(url), { credentials: 'include' });
  if (!response.ok) {
    // A 401 must surface as an error state, never a blank pane.
    throw new Error(`File request failed (${response.status})`);
  }

  const body = response.body;
  if (!body) {
    const whole = await response.text();
    const truncated = whole.length > capBytes;
    return { text: truncated ? whole.slice(0, capBytes) : whole, truncated };
  }

  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: false });
  const chunks: string[] = [];
  let received = 0;
  let truncated = false;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value) {
      received += value.byteLength;
      if (received > capBytes) {
        const keep = value.byteLength - (received - capBytes);
        chunks.push(decoder.decode(value.subarray(0, Math.max(0, keep)), { stream: true }));
        truncated = true;
        await reader.cancel();
        break;
      }
      chunks.push(decoder.decode(value, { stream: true }));
    }
  }
  chunks.push(decoder.decode());
  return { text: chunks.join(''), truncated };
}

/**
 * Query wrapper. The key includes `size_bytes` + `modified_at`, so a
 * re-pushed file arrives under a fresh key with no invalidation plumbing.
 * The cap is also part of the key: a text preview must not supply a truncated
 * cached body to an HTML preview that needs the complete document.
 */
export function useWorkspaceTextQuery(
  ref: WorkspaceFileRef | null | undefined,
  capBytes: number = TEXT_FETCH_CAP_BYTES,
) {
  return useQuery<FetchedText, Error>({
    queryKey: [
      'workspaceText',
      ref?.url ?? '',
      ref?.size_bytes ?? 0,
      ref?.modified_at ?? '',
      capBytes,
    ],
    queryFn: () => fetchWorkspaceText(ref!.url!, capBytes),
    enabled: !!ref?.url,
    staleTime: STALE_TIME.SHORT,
    retry: 1,
  });
}
