/**
 * The new employee's setup screen under the composer (design handoff
 * "Draft"): a checklist while the model writes it, the screen itself when
 * it arrives, or what went wrong with a way to try again.
 *
 * Buttons on the screen act through genui/actions: a change request puts
 * the composer into editing mode, connect buttons open the provider's
 * connect dialog, and Hire goes through useHire. Discard and a finished
 * hire collapse the panel before it goes.
 */

import { Check, Code, X } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { animate, finished } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { isConnected, useConnectors } from '../data/connectors';
import { useHomeStore } from '../state/homeStore';
import { MicroLabel } from '../ui/primitives';
import { pillToast } from '../ui/pillToast';
import { runSpecAction, type ConnectCandidate } from './actions';
import { useDraftActions, useDraftStore, type DraftFailure } from './draftStore';
import { SpecView } from './render';
import { useHire } from './useHire';
import { useReveal } from './useReveal';

const SETUP_STEPS = ['Understanding the job', 'Picking the right apps', 'Writing their routine'] as const;
const STEP_EVERY_MS = 1300;

const FAILURE_DETAIL: Record<DraftFailure['code'], string> = {
  connection: 'The connection dropped part-way.',
  timeout: 'The AI model took too long to answer.',
  provider_error: 'The AI model returned an error.',
  unparseable: 'The AI model’s answer came back unreadable.',
  busy: 'Another setup is still being written.',
  invalid_request: 'That description couldn’t be used.',
  no_ai_provider: 'Connect an AI model first, then try again.',
  cancelled: 'It was stopped.',
};

function WorkingSteps({ token }: { token: string | null }) {
  const [progress, setProgress] = useState({ token, step: 0 });
  const step = progress.token === token ? progress.step : 0;
  useEffect(() => {
    const timer = window.setInterval(() => {
      setProgress((current) => ({
        token,
        step: Math.min((current.token === token ? current.step : 0) + 1, SETUP_STEPS.length - 1),
      }));
    }, STEP_EVERY_MS);
    return () => window.clearInterval(timer);
  }, [token]);
  return (
    <ol aria-label="Setting up" className="flex flex-col gap-3 px-0.5 pt-1 pb-1.5">
      {SETUP_STEPS.map((label, index) => {
        const done = index < step;
        const active = index === step;
        return (
          <li
            key={label}
            aria-current={active ? 'step' : undefined}
            className={cn(
              'flex items-center gap-2.5 text-base transition-colors duration-(--dur-slow)',
              index <= step ? 'text-fg-default' : 'text-fg-faint',
            )}
          >
            {done && (
              <span className="grid size-4.5 shrink-0 place-items-center rounded-full border border-action-run-border bg-action-run-soft text-action-run-ink">
                <Check aria-hidden className="size-2.5" strokeWidth={3.5} />
              </span>
            )}
            {active && (
              <span
                aria-hidden
                className="mx-0.5 size-3.5 shrink-0 animate-spin rounded-full border-2 border-node-agent-border border-t-node-agent motion-reduce:animate-none"
              />
            )}
            {!done && !active && (
              <span aria-hidden className="mx-px size-4 shrink-0 rounded-full border border-dashed border-border-strong" />
            )}
            {label}
          </li>
        );
      })}
    </ol>
  );
}

function FailureNotice({ failure, onRetry, onConnectAi }: { failure: DraftFailure; onRetry: () => void; onConnectAi: () => void }) {
  const needsAi = failure.code === 'no_ai_provider';
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-card border border-status-attention-border bg-status-attention-fill px-3.5 py-3"
    >
      <span aria-hidden className="size-2 shrink-0 rounded-full bg-status-attention-dot" />
      <div className="flex min-w-50 flex-1 flex-col gap-0.5">
        <span className="text-base font-semibold text-fg-default">
          {failure.refine ? 'Couldn’t make that change' : 'Couldn’t finish setting this up'}
        </span>
        <span className="text-sm text-fg-muted">
          {FAILURE_DETAIL[failure.code]} {failure.refine ? 'The last version is still here.' : 'Your description is saved.'}
        </span>
      </div>
      {needsAi && (
        <ActionButton intent="run" onClick={onConnectAi}>
          Connect an AI model
        </ActionButton>
      )}
      {needsAi ? (
        <Button
          variant="quiet"
          onClick={onRetry}
          className="h-8 rounded-md border-border-strong px-3.5 font-semibold text-fg-default"
        >
          Try again
        </Button>
      ) : (
        <ActionButton intent="run" onClick={onRetry}>
          Try again
        </ActionButton>
      )}
    </div>
  );
}

export function HireDraftPanel({ onConnect }: { onConnect: (providerId: string) => void }) {
  const status = useDraftStore((s) => s.status);
  const job = useDraftStore((s) => s.job);
  const token = useDraftStore((s) => s.token);
  const failure = useDraftStore((s) => s.failure);
  const spec = useDraftStore((s) => s.spec);
  const intro = useDraftStore((s) => s.intro);
  const uiState = useDraftStore((s) => s.uiState);
  const apps = useDraftStore((s) => s.apps);
  const version = useDraftStore((s) => s.version);
  const hiring = useDraftStore((s) => s.hiring);
  const actions = useDraftActions();
  const openSettings = useHomeStore((s) => s.openSettings);
  const { providers } = useConnectors();
  const sectionRef = useRef<HTMLElement>(null);
  const introRef = useRef<HTMLParagraphElement>(null);
  const [showJson, setShowJson] = useState(false);

  const visible = status !== 'idle';
  const showSpec = Boolean(spec) && (status === 'ready' || (status === 'failed' && failure?.refine));
  const revealed = useReveal(showSpec && spec ? spec.order : [], version);

  // Enter: rise out of a blur the first time the panel appears.
  const wasVisible = useRef(false);
  useLayoutEffect(() => {
    if (visible && !wasVisible.current) {
      animate(
        sectionRef.current,
        [
          { opacity: 0, transform: 'translateY(18px) scale(.98)', filter: 'blur(4px)' },
          { opacity: 1, transform: 'none', filter: 'blur(0)' },
        ],
        { duration: 560, easing: 'spring' },
      );
    }
    wasVisible.current = visible;
  }, [visible]);

  // The introduction fades up when a new version arrives.
  useLayoutEffect(() => {
    if (status !== 'ready') return;
    animate(introRef.current, [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], {
      duration: 420,
    });
  }, [status, version]);

  const collapse = useCallback(async () => {
    const section = sectionRef.current;
    if (!section) return;
    const height = section.getBoundingClientRect().height;
    await finished(
      animate(
        section,
        [
          { height: `${height}px`, opacity: 1, marginTop: '22px' },
          { height: '0px', opacity: 0, marginTop: '0px' },
        ],
        { duration: 420, easing: 'spring', fill: 'forwards' },
      ),
    );
  }, []);
  const hire = useHire(collapse);

  const candidates = useMemo<ConnectCandidate[]>(
    () => providers.map((provider) => ({ providerId: provider.id, name: provider.name })),
    [providers],
  );

  const connect = useCallback(
    (providerId: string, appName: string) => {
      const provider = providers.find((p) => p.id === providerId);
      if (provider && isConnected(provider)) pillToast(`${appName || provider.name} is already connected`, { tone: 'info' });
      else onConnect(providerId);
    },
    [onConnect, providers],
  );

  const onAction = useCallback(
    (action: string, rawParams: unknown) => {
      runSpecAction(
        action,
        rawParams,
        { state: useDraftStore.getState().uiState, apps, providers: candidates },
        {
          setValue: actions.setValue,
          refine: () => {
            actions.setRefining(true);
            useHomeStore.getState().showHire({ focus: true });
          },
          openConnectors: () => openSettings('connectors'),
          connect,
          hire: (params) => void hire(params),
        },
      );
    },
    [actions, apps, candidates, connect, hire, openSettings],
  );

  const discard = async () => {
    await collapse();
    actions.discard();
  };

  if (!visible) return null;
  return (
    <section
      ref={sectionRef}
      aria-label="New employee"
      aria-busy={status === 'working' || hiring}
      className="mt-5.5 w-full max-w-(--w-composer) overflow-hidden"
    >
      <div className="flex flex-col gap-3.5 rounded-draft border border-border-default bg-bg-panel p-4.5 shadow-float">
        <div className="flex items-center gap-2.5">
          <MicroLabel className="shrink-0 text-node-agent-ink">New employee</MicroLabel>
          <span className="min-w-0 flex-1 truncate text-sm text-fg-muted">{job}</span>
          <Button variant="quiet" size="icon-sm" onClick={() => void discard()} title="Discard draft" aria-label="Discard draft">
            <X />
          </Button>
        </div>

        {status === 'working' && <WorkingSteps token={token} />}
        {status === 'failed' && failure && (
          <FailureNotice
            failure={failure}
            onRetry={() => void actions.retry()}
            onConnectAi={() => openSettings('connectors', 'ai')}
          />
        )}
        {status === 'ready' && intro && (
          <p ref={introRef} className="m-0 text-lead leading-relaxed text-pretty text-fg-default">
            {intro}
          </p>
        )}
        {showSpec && spec && (
          <>
            <div className={cn('flex min-w-0 flex-col gap-3', hiring && 'pointer-events-none opacity-60')}>
              <SpecView spec={spec} state={uiState} revealed={revealed} onAction={onAction} onValue={actions.setValue} />
            </div>
            <Button
              variant="quiet"
              size="xs"
              onClick={() => setShowJson((on) => !on)}
              title="See the layout the assistant generated"
              aria-expanded={showJson}
              className="self-start font-mono text-2xs font-normal text-fg-faint hover:border-border-default"
            >
              <Code aria-hidden />
              {showJson ? 'Hide layout JSON' : 'Layout JSON'}
            </Button>
            {showJson && (
              <pre className="m-0 max-h-70 overflow-auto rounded-row border border-border-default bg-bg-app px-3.5 py-3 font-mono text-2xs leading-normal whitespace-pre text-fg-muted">
                {JSON.stringify(spec, null, 2)}
              </pre>
            )}
          </>
        )}
      </div>
    </section>
  );
}

export default HireDraftPanel;
