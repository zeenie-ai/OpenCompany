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

import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Spec } from '@json-render/core';
import { JSONUIProvider, Renderer } from '@json-render/react';
import { LiveUiContext, createUiStateStore, useSpecReveal, type UiStateChange } from '@/lib/jsonRender';
import { chatActionHandlers, type ChatUiActions } from './actions';
import { prepareChatSpec } from './prepare';
import { chatRegistry } from './registry';

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

function ChatUiBody({ partId, spec, state, live, actions, onStateChange }: Omit<ChatUiProps, 'spec'> & { spec: Spec }) {
  const { spec: shown, revealing } = useSpecReveal(spec, live);

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
        <Renderer spec={shown} registry={chatRegistry} loading={revealing} fallback={NoElement} />
        {revealing && (
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
