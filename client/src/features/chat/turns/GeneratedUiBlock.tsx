/**
 * An interface the employee showed in its reply (design handoff chat,
 * "Generated UI"), under the reply's text. The renderer loads lazily
 * (genui/ChatUi.tsx, with json-render); until it has, a dashed shimmer
 * stands where the interface will be. Development builds also show, on one
 * line, its size and an Inspect chip for the spec and patches it came from.
 *
 * `chat-genui` is the theme hook for the block.
 */

import { Suspense, lazy, useMemo } from 'react';
import { specToPatches } from '@/lib/jsonRender/reveal';
import { SpecInspector } from '@/lib/jsonRender/SpecInspector';
import type { UiStateChange } from '@/lib/jsonRender/uiState';
import type { UiPart } from '../data/parts';
import type { ChatUiActions } from '../genui/actions';
import { prepareChatSpec } from '../genui/prepare';

const ChatUi = lazy(() => import('../genui/ChatUi'));

function DevDetails({ part }: { part: UiPart }) {
  const spec = useMemo(() => prepareChatSpec(part.spec), [part.spec]);
  // The count matches what the inspector's patches tab lists.
  const patches = useMemo(() => (spec ? specToPatches(spec).length : 0), [spec]);
  if (!spec) return null;
  const elements = Object.keys(spec.elements).length;
  return (
    <SpecInspector
      spec={spec}
      label="Inspect"
      meta={
        <span className="font-mono text-2xs text-fg-faint">
          {elements} elements · {patches} patches
        </span>
      }
    />
  );
}

export function GeneratedUiBlock({
  part,
  live,
  actions,
  onStateChange,
}: {
  part: UiPart;
  live: boolean;
  actions: ChatUiActions;
  onStateChange?: (partId: string, changes: UiStateChange[]) => void;
}) {
  return (
    <div data-part={part.partId} className="chat-genui flex min-w-0 flex-col gap-2">
      <Suspense fallback={<div aria-hidden className="opencompany-shimmer-slot h-20 rounded-xl border border-dashed border-border-default" />}>
        <ChatUi
          partId={part.partId}
          spec={part.spec}
          state={part.state}
          live={live}
          actions={actions}
          onStateChange={onStateChange ? (changes) => onStateChange(part.partId, changes) : undefined}
        />
      </Suspense>
      {import.meta.env.DEV && <DevDetails part={part} />}
    </div>
  );
}
