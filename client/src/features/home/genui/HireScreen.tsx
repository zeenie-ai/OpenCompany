/**
 * The setup screen itself, drawn by json-render from the hire catalogue
 * (registry.ts). HireDraftPanel loads it lazily, the first time a screen
 * shows, so json-render stays out of Home's first chunk; it renders one per
 * version of the draft (keyed by it), so a new version starts afresh.
 *
 * The card (onboarding handoff R2) is drawn in the places the normaliser
 * names (`spec.layout`), each a Renderer over the same spec under one
 * provider, so all of them share the screen's state and handlers: the
 * identity row with Discard beside it, the body (the routine, then the
 * rest), the panel's own `children` (their team), then the footer strip,
 * which bleeds to the card's edges, with Ask first on the left and the
 * buttons on the right, both in the strip's look (HireSpecContext.inFooter).
 *
 * The screen keeps its own state store (lib/jsonRender/uiState.ts), seeded
 * from the draft (what the model wrote, plus anything the owner already
 * changed on this version), and every change goes back to the draft
 * (setDraftValue), which Hire reads. Buttons run the hire actions
 * (actions.ts); setState is json-render's own. The screen appears one
 * element at a time (useSpecReveal) and each element rises in once.
 * Development builds also show the spec and its patch stream.
 */

import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
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
  /** Drawn at the end of the identity row (the panel's Discard), or nothing. */
  discard: ReactNode;
  /** Drawn after the body, above the footer strip. */
  children?: ReactNode;
}

/** Unknown types are dropped by the normaliser; anything else renders nothing. */
function NoElement() {
  return null;
}

export default function HireScreen({ spec, version, busy, triggerApps, actions, discard, children }: HireScreenProps) {
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
  const footer = useMemo(() => ({ ...context, inFooter: true }), [context]);
  const { layout } = spec;

  /** The spec drawn from one element, once the reveal has reached it. */
  const part = (root: string) =>
    shown && <Renderer key={root} spec={{ ...shown, root }} registry={hireRegistry} loading={revealing} fallback={NoElement} />;

  return (
    <div className={cn('flex min-w-0 flex-col gap-3.5', busy && 'pointer-events-none opacity-60')}>
      <HireSpecContext.Provider value={context}>
        <LiveUiContext.Provider value>
          <JSONUIProvider registry={hireRegistry} store={store} handlers={handlers}>
            {layout.identity && (
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">{part(layout.identity)}</div>
                {discard}
              </div>
            )}
            {layout.body.map((id) => part(id))}
            {children}
            {import.meta.env.DEV && devMode && <SpecInspector spec={uiSpec} />}
            <HireSpecContext.Provider value={footer}>
              <div className="-mx-4.5 -mb-4.5 flex flex-wrap items-center gap-x-4 gap-y-2.5 rounded-b-draft border-t border-border-default bg-bg-app px-4.5 py-3.5">
                {part(layout.askFirst)}
                <div className="ml-auto">{part(layout.actions)}</div>
              </div>
            </HireSpecContext.Provider>
          </JSONUIProvider>
        </LiveUiContext.Provider>
      </HireSpecContext.Provider>
    </div>
  );
}
