/** One labelled row of a Settings page: title and detail, its control on the
 *  right (Profile's switches, Help's replay). */

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function SettingRow({
  title,
  detail,
  children,
  last = false,
}: {
  title: string;
  detail: string;
  children: ReactNode;
  last?: boolean;
}) {
  return (
    <div className={cn('flex items-center gap-4 border-t border-border-default py-3.5', last && 'border-b')}>
      <div className="flex flex-1 flex-col gap-0.75">
        <span className="text-base font-medium text-fg-default">{title}</span>
        <span className="text-meta text-fg-muted">{detail}</span>
      </div>
      {children}
    </div>
  );
}

export default SettingRow;
