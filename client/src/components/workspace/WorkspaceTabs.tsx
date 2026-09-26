import { Globe, PanelsTopLeft, Smartphone } from 'lucide-react';
import { Tabs as TabsPrimitive } from 'radix-ui';
import type { ReactNode } from 'react';

export type WorkspaceTab = 'browser' | 'board' | 'android';

/** Shared navigation and sizing contract for Home and the workflow editor. */
export function WorkspaceTabs({ tab, onTabChange, browser, board, android }: {
  tab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  browser: ReactNode;
  board: ReactNode;
  android?: ReactNode;
}) {
  return (
    <TabsPrimitive.Root value={tab} onValueChange={(value) => onTabChange(value as WorkspaceTab)} className="flex min-h-0 min-w-0 flex-1 flex-col">
      <TabsPrimitive.List aria-label="Workspace views" className="flex h-10 shrink-0 gap-0.5 overflow-x-auto border-b border-border-default px-2">
        {([
          ['browser', 'Browser', Globe],
          ['board', 'Canvas', PanelsTopLeft],
          ['android', 'Android', Smartphone],
        ] as const).map(([value, label, Icon]) => (
          <TabsPrimitive.Trigger key={value} value={value} className="flex h-10 items-center gap-1.75 whitespace-nowrap border-b-2 border-transparent px-2.5 text-sm font-medium text-fg-muted outline-none transition-colors hover:text-fg-default focus-visible:ring-3 focus-visible:ring-ring/50 data-[state=active]:border-node-agent data-[state=active]:text-fg-default">
            <Icon aria-hidden className="size-3.75" strokeWidth={1.75} />{label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {([
        ['browser', browser], ['board', board], ['android', android ?? <AndroidWorkspace />],
      ] as const).map(([value, content]) => (
        <TabsPrimitive.Content key={value} value={value} className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-bg-app p-3 outline-none">
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
