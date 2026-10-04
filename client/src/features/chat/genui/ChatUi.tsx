/**
 * One interface an employee showed in a reply, drawn by json-render from the
 * chat catalogue (registry.ts). The reply turn loads it lazily
 * (turns/GeneratedUiBlock.tsx), so json-render stays out of the chat's
 * first chunk.
 *
 * - **Arriving.** While the run streams it (`live`), the elements appear one
 *   at a time (REVEAL_STEP_MS each, all at once under reduced motion or on a
 *   hidden page) and each rises in once, a dashed slot below them standing
 *   for the ones still to come. The count only moves forward, so patches
 *   that land in several bursts never restart it. A UI read back later shows
 *   at once, without motion.
 * - **State.** It keeps its own state store (lib/jsonRender/uiState.ts),
 *   seeded from what the owner last set (`state`) or the spec's own, and
 *   reports every change to `onStateChange`.
 * - **Buttons** run the chat's actions (genui/actions.ts); `setState` is
 *   json-render's own.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Spec } from '@json-render/core';
import { JSONUIProvider, Renderer } from '@json-render/react';
import { LiveUiContext, REVEAL_STEP_MS, createUiStateStore, specFromPatches, specToPatches, type UiStateChange } from '@/lib/jsonRender';
import { motionSuppressed } from '@/lib/motion';
import { chatActionHandlers, type ChatUiActions } from './actions';
import { prepareChatSpec } from './prepare';
import { chatRegistry } from './registry';

/** `add /root` and `add /state`, ahead of the elements. */
const HEAD = 2;

export interface ChatUiProps {
  partId: string;
  /** The spec as the run or the saved reply has it. */
  spec: unknown;
  /** What the owner last set, when the reply was saved with it. */
  state?: Record<string, unknown> | null;
  live: boolean;
  actions: ChatUiActions;
  onStateChange?: (changes: UiStateChange[]) => void;
}

/** Unknown types are left out by prepareChatSpec; anything else renders nothing. */
function NoElement() {
  return null;
}

/** How many of `total` elements show: forward only, paced from when the
 *  first one showed. */
function useForwardReveal(total: number, live: boolean): number {
  const [shown, setShown] = useState(() => (!live || motionSuppressed() ? Number.POSITIVE_INFINITY : 1));
  const started = useRef<number | null>(null);

  useEffect(() => {
    if (shown >= total) return;
    const since = started.current ?? performance.now();
    started.current = since;
    const timer = window.setInterval(() => {
      const due = motionSuppressed() ? total : Math.floor((performance.now() - since) / REVEAL_STEP_MS) + 1;
      setShown((current) => Math.max(current, Math.min(total, due)));
    }, REVEAL_STEP_MS);
    return () => window.clearInterval(timer);
  }, [shown, total]);
  return Math.min(total, shown);
}

function ChatUiBody({ partId, spec, state, live, actions, onStateChange }: Omit<ChatUiProps, 'spec'> & { spec: Spec }) {
  const patches = useMemo(() => specToPatches(spec), [spec]);
  const total = Math.max(0, patches.length - HEAD);
  const count = useForwardReveal(total, live);
  const shown = useMemo(() => (count >= total ? spec : specFromPatches(patches, HEAD + count)), [count, total, spec, patches]);

  const latest = useRef({ actions, onStateChange });
  useLayoutEffect(() => {
    latest.current = { actions, onStateChange };
  });
  const [store] = useState(() =>
    createUiStateStore(state ?? spec.state ?? {}, (changes) => latest.current.onStateChange?.(changes)),
  );
  const [handlers] = useState(() => chatActionHandlers(partId, () => latest.current.actions));

  return (
    <LiveUiContext.Provider value={live}>
      <JSONUIProvider registry={chatRegistry} store={store} handlers={handlers}>
        <Renderer spec={shown} registry={chatRegistry} loading={count < total} fallback={NoElement} />
        {count < total && (
          <div aria-hidden data-slot="next" className="opencompany-shimmer-slot h-9 rounded-xl border border-dashed border-border-default" />
        )}
      </JSONUIProvider>
    </LiveUiContext.Provider>
  );
}

export default function ChatUi(props: ChatUiProps) {
  const spec = useMemo(() => prepareChatSpec(props.spec), [props.spec]);
  // Nothing until the root has arrived: by then its state has too.
  if (!spec) return null;
  return <ChatUiBody {...props} spec={spec} />;
}
