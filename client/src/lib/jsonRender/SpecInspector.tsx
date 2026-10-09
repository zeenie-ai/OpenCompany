/**
 * A developer's view of a generated UI: the spec as JSON, and the patch
 * stream that builds it (one JSON patch per line, as the chat protocol
 * streams it). Render it only in development builds
 * (`import.meta.env.DEV && <SpecInspector ... />`) so release builds drop it.
 *
 * The trigger is a small pressed/unpressed chip (design handoff Chat v2,
 * the dev line); `meta` sits beside it on the same row.
 */

import { Code } from 'lucide-react';
import { type ReactNode, useId, useMemo, useState } from 'react';
import type { Spec } from '@json-render/core';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { specToPatches } from './reveal';

const CODE_CLASS =
  'm-0 max-h-70 overflow-auto rounded-row border border-border-default bg-bg-app px-3.5 py-3 font-mono text-2xs leading-normal whitespace-pre text-fg-muted';

export function SpecInspector({ spec, label = 'Layout JSON', meta }: { spec: Spec; label?: string; meta?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const specJson = useMemo(() => JSON.stringify(spec, null, 2), [spec]);
  const patchLines = useMemo(
    () =>
      specToPatches(spec)
        .map((patch) => JSON.stringify(patch))
        .join('\n'),
    [spec],
  );
  return (
    <div className="flex flex-col gap-2">
      <div className="flex min-w-0 items-center gap-2">
        {meta}
        <Button
          variant="quiet"
          size="xs"
          onClick={() => setOpen((on) => !on)}
          title="See what the assistant generated"
          aria-pressed={open}
          aria-controls={open ? panelId : undefined}
          className="h-5.5 gap-1.25 rounded-md border-border-default px-1.75 font-mono text-2xs text-fg-muted hover:border-border-strong hover:bg-transparent hover:text-fg-default aria-pressed:bg-bg-active"
        >
          <Code aria-hidden className="size-2.5" />
          {label}
        </Button>
      </div>
      {open && (
        <Tabs id={panelId} defaultValue="spec" className="gap-2">
          <TabsList variant="line" className="h-7">
            <TabsTrigger value="spec" className="font-mono text-2xs">
              spec.json
            </TabsTrigger>
            <TabsTrigger value="patches" className="font-mono text-2xs">
              patches.jsonl
            </TabsTrigger>
          </TabsList>
          <TabsContent value="spec">
            <pre className={CODE_CLASS}>{specJson}</pre>
          </TabsContent>
          <TabsContent value="patches">
            <pre className={CODE_CLASS}>{patchLines}</pre>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
