/**
 * The setup screen itself, drawn by json-render from the hire catalogue
 * (registry.ts). HireDraftPanel loads it lazily, the first time a screen
 * shows, so json-render stays out of Home's first chunk; it renders one per
 * version of the draft (keyed by it), so a new version starts afresh.
 *
 * The screen keeps its own state store (lib/jsonRender/uiState.ts), seeded
 * from the draft (what the model wrote, plus anything the owner already
 * changed on this version), and every change goes back to the draft
 * (setDraftValue), which Hire reads. Buttons run the hire actions
 * (actions.ts); setState is json-render's own. The screen appears one
 * element at a time (useSpecReveal) and each element rises in once.
 * Development builds also show the spec and its patch stream.
 */

import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Spec } from '@json-render/core';
import { JSONUIProvider, Renderer } from '@json-render/react';
import { LiveUiContext, SpecInspector, createUiStateStore, useSpecReveal } from '@/lib/jsonRender';
import { cn } from '@/lib/utils';
import { useAppStore } from '@/store/useAppStore';
import { hireActionHandlers, type HireActionContext } from './actions';
import { setDraftValue, useDraftStore } from './draftStore';
import { HireSpecContext } from './hireSpecContext';
import type { NormalizedSpec } from './normalize';
import { hireRegistry } from './registry';

export interface HireScreenProps {
  spec: NormalizedSpec;
  version: number;
  /** A hire is in flight: the screen dims and takes no input. */
  busy: boolean;
  /** Apps that can start the work: any name the reply used -> the app's own name. */
  triggerApps: Readonly<Record<string, string>>;
  /** What the buttons reach on Home; read at each press. */
  actions: HireActionContext;
}

/** Unknown types are dropped by the normaliser; anything else renders nothing. */
function NoElement() {
  return null;
}

export default function HireScreen({ spec, version, busy, triggerApps, actions }: HireScreenProps) {
  const devMode = useAppStore((state) => state.shellMode === 'dev');
  const uiSpec = useMemo<Spec>(() => ({ root: spec.root, state: spec.state, elements: spec.elements }), [spec]);
  const { spec: shown, revealing } = useSpecReveal(uiSpec, version);
  const [store] = useState(() =>
    createUiStateStore(useDraftStore.getState().uiState, (changes) => {
      for (const { path, value } of changes) setDraftValue(path, value);
    }),
  );
  const latest = useRef(actions);
  useLayoutEffect(() => {
    latest.current = actions;
  });
  const [handlers] = useState(() => hireActionHandlers(() => latest.current));
  const context = useMemo(() => ({ writtenState: spec.state, triggerApps }), [spec.state, triggerApps]);

  return (
    <>
      <div className={cn('flex min-w-0 flex-col gap-3', busy && 'pointer-events-none opacity-60')}>
        <HireSpecContext.Provider value={context}>
          <LiveUiContext.Provider value>
            <JSONUIProvider registry={hireRegistry} store={store} handlers={handlers}>
              <Renderer spec={shown} registry={hireRegistry} loading={revealing} fallback={NoElement} />
            </JSONUIProvider>
          </LiveUiContext.Provider>
        </HireSpecContext.Provider>
      </div>
      {import.meta.env.DEV && devMode && <SpecInspector spec={uiSpec} />}
    </>
  );
}
