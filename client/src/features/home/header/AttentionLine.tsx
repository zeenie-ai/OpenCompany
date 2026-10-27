/**
 * One line under the Home header while work keeps retrying past its limit
 * (data/attention.ts). It speaks of tasks, not employees: an item names a
 * Temporal activity. Nothing shows while nothing is open.
 */

import { TriangleAlert } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useAttentionItems, type AttentionItem } from '../data/attention';

function describe(items: AttentionItem[]): string {
  const stuck = items.filter((item) => item.kind === 'stuck_retry').length;
  const other = items.length - stuck;
  const parts: string[] = [];
  if (stuck) parts.push(stuck === 1 ? 'A task keeps retrying and may be stuck.' : `${stuck} tasks keep retrying and may be stuck.`);
  if (other) parts.push(other === 1 ? 'Something else needs a look.' : `${other} other things need a look.`);
  return parts.join(' ');
}

export function AttentionLine() {
  const { data: items } = useAttentionItems();
  if (!items?.length) return null;
  return (
    <div className="relative z-10 shrink-0 px-3.5 pb-2">
      <Alert variant="warning">
        <TriangleAlert />
        <AlertDescription>{describe(items)}</AlertDescription>
      </Alert>
    </div>
  );
}
