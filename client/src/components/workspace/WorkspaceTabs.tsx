import { Globe, PanelsTopLeft, Smartphone } from 'lucide-react';
import { Tabs as TabsPrimitive } from 'radix-ui';
import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { animate } from '@/lib/motion';

export type WorkspaceTab = 'browser' | 'board' | 'android';

/** The body arriving on a tab switch (design handoff "Workspace panel"):
 *  it fades and rises out of a blur. */
const SWAP: Keyframe[] = [
  { opacity: 0, transform: 'translateY(8px)', filter: 'blur(4px)' },
  { opacity: 1, transform: 'none', filter: 'none' },
];

/** Shared navigation and sizing contract for Home and the workflow editor.
 *  `activity` marks the surfaces with something going on; an inactive tab
 *  with activity shows a dot (components/workspace/activity.ts). */
export function WorkspaceTabs({ tab, onTabChange, browser, board, android, activity }: {
  tab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  browser: ReactNode;
  board: ReactNode;
  android?: ReactNode;
  activity?: Partial<Record<WorkspaceTab, boolean>>;
}) {
  const bodies = useRef<Partial<Record<WorkspaceTab, HTMLDivElement | null>>>({});
  const shown = useRef(tab);
  useLayoutEffect(() => {
    if (shown.current === tab) return;
    shown.current = tab;
    animate(bodies.current[tab], SWAP, { duration: 'slow', easing: 'spring' });
  }, [tab]);

  return (
    <TabsPrimitive.Root value={tab} onValueChange={(value) => onTabChange(value as WorkspaceTab)} className="flex min-h-0 min-w-0 flex-1 flex-col">
      <TabsPrimitive.List aria-label="Workspace views" className="flex h-10 shrink-0 gap-0.5 overflow-x-auto overflow-y-hidden border-b border-border-default px-2">
        {([
          ['browser', 'Browser', Globe],
          ['board', 'Canvas', PanelsTopLeft],
          ['android', 'Mobile', Smartphone],
        ] as const).map(([value, label, Icon]) => (
          <TabsPrimitive.Trigger key={value} value={value} className="flex h-10 items-center gap-1.75 whitespace-nowrap border-b-2 border-transparent px-2.5 text-sm font-medium text-fg-muted outline-none transition-colors hover:text-fg-default focus-visible:ring-3 focus-visible:ring-ring/50 data-[state=active]:border-node-agent data-[state=active]:text-fg-default">
            <Icon aria-hidden className="size-3.75" strokeWidth={1.75} />{label}
            {activity?.[value] && value !== tab && (
              <>
                <span aria-hidden data-slot="workspace-tab-dot" className="size-1.5 rounded-full bg-node-trigger opencompany-pip-pulse" />
                <span className="sr-only"> (busy)</span>
              </>
            )}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {([
        ['browser', browser], ['board', board], ['android', android ?? <AndroidWorkspace />],
      ] as const).map(([value, content]) => (
        <TabsPrimitive.Content
          key={value}
          value={value}
          ref={(element) => { bodies.current[value] = element; }}
          className={`flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-bg-app outline-none ${value === 'board' ? 'p-3' : ''}`}
        >
          {content}
        </TabsPrimitive.Content>
      ))}
    </TabsPrimitive.Root>
  );
}

export function AndroidWorkspace() {
  return (
    <div className="m-auto flex max-w-80 flex-col items-center gap-2 p-6 text-center">
      <Smartphone aria-hidden className="size-6 text-fg-faint" strokeWidth={1.5} />
      <p className="m-0 text-base font-medium text-fg-default">The Android mirror isn’t available yet</p>
      <p className="m-0 text-sm text-fg-muted">Android actions can run in your workflow. A live device view is not available yet.</p>
    </div>
  );
}
