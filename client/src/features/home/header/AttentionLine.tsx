/**
 * One line under the Home header while work keeps retrying past its limit,
 * or runs are stuck on failing workflow tasks (data/attention.ts). It speaks
 * of tasks and runs, not employees: an item names a Temporal activity, or
 * the engine as a whole. Nothing shows while nothing is open.
 */

import { TriangleAlert } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useAttentionItems, type AttentionItem } from '../data/attention';

function describe(items: AttentionItem[]): string {
  const stuck = items.filter((item) => item.kind === 'stuck_retry').length;
  const stuckRuns = items.some((item) => item.kind === 'stuck_runs');
  const other = items.length - stuck - (stuckRuns ? 1 : 0);
  const parts: string[] = [];
  if (stuck) parts.push(stuck === 1 ? 'A task keeps retrying and may be stuck.' : `${stuck} tasks keep retrying and may be stuck.`);
  if (stuckRuns) parts.push('Some runs keep failing in the engine and are stuck.');
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
