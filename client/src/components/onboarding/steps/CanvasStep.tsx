import React from 'react';
import { Layout, Wrench, Terminal } from 'lucide-react';
import { Badge } from '@/components/ui/badge';

const CanvasStep: React.FC = () => {
  const shortcuts = [
    { keys: 'Ctrl+S', action: 'Save workflow' },
    { keys: 'F2', action: 'Rename node' },
    { keys: 'Delete', action: 'Remove node' },
    { keys: 'Ctrl+C', action: 'Copy node' },
  ];

  return (
    <div className="py-1">
      <div className="mb-4 text-center">
        <h4 className="m-0 mb-1 text-lg font-semibold">Canvas Tour</h4>
        <p className="text-xs text-muted-foreground">
          Navigate the main interface regions
        </p>
      </div>

      {/*
       * Visual layout diagram. Region tints mirror the live UI's
       * accent colours via the predefined --node-X-soft tokens — no
       * opacity arithmetic at the call site. Each region maps to a
       * node-group colour: toolbar→workflow, sidebar→model,
       * canvas→agent, palette→skill, console→trigger. Both the region
       * tints (--node-X-soft) and the text/icon accents (text-node-X)
       * use the role tokens, so the diagram tracks every theme.
       */}
      <div className="mb-4 overflow-hidden rounded-lg border border-border">
        {/* Toolbar */}
        <div className="flex items-center gap-1.5 border-b border-border bg-node-workflow-soft px-2.5 py-1.5">
          <Wrench className="h-3 w-3 text-node-workflow" />
          <span className="text-xs font-semibold text-node-workflow">Toolbar</span>
          <div className="flex-1" />
          <Badge variant="warning" className="text-[10px]">Run</Badge>
          <Badge variant="secondary" className="text-[10px]">Start</Badge>
          <Badge variant="info" className="text-[10px]">Save</Badge>
        </div>

        {/* Main area */}
        <div className="flex h-[120px]">
          {/* Sidebar */}
          <div className="flex w-20 flex-col gap-1 border-r border-border bg-node-model-soft p-1.5">
            <span className="text-[9px] font-semibold text-node-model">Sidebar</span>
            {['Workflow 1', 'Workflow 2'].map((w) => (
              <div key={w} className="rounded-sm bg-muted px-1 py-0.5 text-[8px] text-muted-foreground">
                {w}
              </div>
            ))}
          </div>

          {/* Canvas */}
          <div className="relative flex flex-1 items-center justify-center bg-node-agent-soft">
            <div className="text-center">
              <Layout className="mx-auto h-5 w-5 text-node-agent opacity-50" />
              <div className="mt-0.5 text-[9px] text-node-agent">Canvas</div>
            </div>
          </div>

          {/* Palette */}
          <div className="flex w-20 flex-col gap-1 border-l border-border bg-node-skill-soft p-1.5">
            <span className="text-[9px] font-semibold text-node-skill">Palette</span>
            {['AI Agents', 'AI Models', 'Skills'].map((c) => (
              <div key={c} className="rounded-sm bg-muted px-1 py-0.5 text-[8px] text-muted-foreground">
                {c}
              </div>
            ))}
          </div>
        </div>

        {/* Console */}
        <div className="flex items-center gap-1.5 border-t border-border bg-node-trigger-soft px-2.5 py-1.5">
          <Terminal className="h-3 w-3 text-node-trigger" />
          <span className="text-xs font-semibold text-node-trigger">Console</span>
          <div className="flex-1" />
          <Badge variant="outline" className="text-[9px]">Chat</Badge>
          <Badge variant="outline" className="text-[9px]">Logs</Badge>
        </div>
      </div>

      {/* Keyboard shortcuts */}
      <div>
        <div className="mb-2 block text-xs font-semibold">Keyboard Shortcuts</div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          {shortcuts.map((s) => (
            <span key={s.keys} className="inline-flex items-center gap-1.5 text-xs">
              <Badge variant="outline" className="font-mono text-[11px]">
                {s.keys}
              </Badge>
              <span className="text-xs text-muted-foreground">{s.action}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
};

export default CanvasStep;
