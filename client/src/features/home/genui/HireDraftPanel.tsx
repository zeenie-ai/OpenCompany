/**
 * The new employee's setup screen under the composer (design handoff
 * "Draft"): while the model writes it, a line saying so with the time it
 * has taken so far and Cancel; the screen itself when it arrives; or what
 * went wrong with a way to try again. The header (what they were asked to
 * do, and Discard) shows while the setup is written or has failed; a ready
 * card says who they are in its identity row instead, with Discard beside
 * it (onboarding handoff R2). Their team, when the server suggests one,
 * sits in the card above its footer strip.
 *
 * The screen is drawn by json-render (HireScreen, loaded lazily the first
 * time one shows: json-render is not part of Home's first chunk). Its
 * buttons act through genui/actions: a change request puts the composer
 * into editing mode, connect buttons open the provider's connect dialog,
 * and Hire goes through useHire. Discard and a finished hire collapse the
 * panel before it goes. The spec and its patch stream are a developer's
 * view, shown in development builds only.
 */

import { X } from 'lucide-react';
import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import { RingSpinner } from '@/components/ui/ring-spinner';
import { animate, finished } from '@/lib/motion';
import { isConnected, useConnectors } from '../data/connectors';
import { useHomeStore } from '../state/homeStore';
import { MicroLabel } from '../ui/primitives';
import { pillToast } from '../ui/pillToast';
import type { ConnectCandidate, HireActionContext } from './actions';
import { triggerAppNames, useDraftActions, useDraftStore, type DraftFailure } from './draftStore';
import { useHire } from './useHire';

const loadHireScreen = () => import('./HireScreen');
const HireScreen = lazy(loadHireScreen);

/** After this long, the line says some models take a few minutes. */
const SLOW_AFTER_SECONDS = 45;

const FAILURE_DETAIL: Record<DraftFailure['code'], string> = {
  connection: 'The connection dropped part-way.',
  timeout: 'The AI model took too long to answer.',
  provider_error: 'The AI model returned an error.',
  unparseable: 'The AI model’s answer came back unreadable.',
  busy: 'Another setup is still being written.',
  invalid_request: 'That description couldn’t be used.',
  no_ai_provider: 'Connect an AI model first. The setup carries on once one is connected.',
  cancelled: 'It was stopped.',
};

/** Whole seconds since the request with this token started. */
function useElapsed(token: string | null): number {
  const [clock, setClock] = useState({ token, seconds: 0 });
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(() => {
      setClock({ token, seconds: Math.floor((Date.now() - started) / 1000) });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [token]);
  return clock.token === token ? clock.seconds : 0;
}

function clockText(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function WorkingLine({ token, onCancel }: { token: string | null; onCancel: () => void }) {
  const seconds = useElapsed(token);
  return (
    <div className="flex flex-col gap-1.5 px-0.5 py-1">
      <div className="flex items-center gap-2.5">
        <RingSpinner className="mx-0.5 size-3.5 border-node-agent-border border-t-node-agent" />
        <span role="status" className="text-base text-fg-default">
          Writing their setup…
        </span>
        <span role="timer" aria-label="Time so far" className="font-mono text-xs text-fg-faint">
          {clockText(seconds)}
        </span>
        <Button
          variant="quiet"
          onClick={onCancel}
          className="ml-auto h-8 rounded-lg border-border-strong px-3.5 font-semibold text-fg-default"
        >
          Cancel
        </Button>
      </div>
      {seconds >= SLOW_AFTER_SECONDS && (
        <p className="m-0 text-sm text-fg-muted">
          Some AI models take a few minutes, especially ones running on this computer. You can keep waiting, or
          cancel and change the description.
        </p>
      )}
    </div>
  );
}

function FailureNotice({ failure, onRetry, onConnectAi }: { failure: DraftFailure; onRetry: () => void; onConnectAi: () => void }) {
  const needsAi = failure.code === 'no_ai_provider';
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-card border border-node-trigger-border bg-node-trigger-soft px-3.5 py-3"
    >
      <span aria-hidden className="size-2 shrink-0 rounded-full bg-node-trigger" />
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
          className="h-8 rounded-lg border-border-strong px-3.5 font-semibold text-fg-default"
        >
          Try again
        </Button>
      ) : (
        <ActionButton intent="stop" onClick={onRetry} className="rounded-lg">
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
  const apps = useDraftStore((s) => s.apps);
  const team = useDraftStore((s) => s.team);
  const version = useDraftStore((s) => s.version);
  const hiring = useDraftStore((s) => s.hiring);
  const actions = useDraftActions();
  const openSettings = useHomeStore((s) => s.openSettings);
  const openConnectAI = useHomeStore((s) => s.openConnectAI);
  const { providers } = useConnectors();
  const sectionRef = useRef<HTMLElement>(null);

  const visible = status !== 'idle';
  const showSpec = Boolean(spec) && (status === 'ready' || (status === 'failed' && failure?.refine));
  const showHeader = status !== 'ready' || !spec?.layout.identity;
  const triggerApps = useMemo(() => triggerAppNames(apps), [apps]);

  // Fetch the screen's code while the model writes, so it is there when the reply lands.
  useEffect(() => {
    if (status === 'working') void loadHireScreen();
  }, [status]);

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

  const actionContext = useMemo<HireActionContext>(
    () => ({
      apps,
      providers: candidates,
      actions: {
        refine: () => {
          actions.setRefining(true);
          useHomeStore.getState().showHire({ focus: true });
        },
        openConnectors: () => openSettings('connectors'),
        connect,
        hire: (params) => void hire(params),
      },
    }),
    [actions, apps, candidates, connect, hire, openSettings],
  );

  const discard = async () => {
    await collapse();
    actions.discard();
  };
  const discardButton = (
    <Button
      variant="quiet"
      size="icon-sm"
      onClick={() => void discard()}
      title="Discard draft"
      aria-label="Discard draft"
      className="rounded-lg"
    >
      <X className="size-3.5" />
    </Button>
  );

  if (!visible) return null;
  return (
    <section
      ref={sectionRef}
      aria-label="New employee"
      aria-busy={status === 'working' || hiring}
      className="mt-5.5 w-full max-w-(--w-composer) overflow-hidden"
    >
      <div className="flex flex-col gap-3.5 rounded-draft border border-border-default bg-bg-panel p-4.5 shadow-float">
        {showHeader && (
          <div className="flex items-center gap-2.5">
            <MicroLabel className="shrink-0 text-node-agent-ink">New employee</MicroLabel>
            <span className="min-w-0 flex-1 truncate text-sm text-fg-muted">{job}</span>
            {discardButton}
          </div>
        )}

        {status === 'working' && <WorkingLine token={token} onCancel={actions.cancel} />}
        {hiring && (
          <p role="status" className="m-0 text-sm text-fg-muted">
            Hiring your employee…
          </p>
        )}
        {status === 'failed' && failure && (
          <FailureNotice failure={failure} onRetry={() => void actions.retry()} onConnectAi={openConnectAI} />
        )}
        {showSpec && spec && (
          <Suspense fallback={null}>
            <HireScreen
              key={version}
              spec={spec}
              version={version}
              busy={hiring}
              triggerApps={triggerApps}
              actions={actionContext}
              discard={showHeader ? null : discardButton}
            >
              {team.length > 0 && (
                <details className="rounded-row border border-border-default px-3.5 py-3 text-sm text-fg-muted">
                  <summary className="cursor-pointer font-semibold text-fg-default">Their team</summary>
                  <p className="mt-2 mb-2">They can ask these helpers to do parts of the job, then check the result for you.</p>
                  <ul className="m-0 list-disc space-y-1 pl-5">
                    {team.map(({ responsibility }) => <li key={responsibility}>{responsibility}</li>)}
                  </ul>
                  <p className="mt-2 mb-0">Use Change something to adjust their responsibilities.</p>
                </details>
              )}
            </HireScreen>
          </Suspense>
        )}
      </div>
    </section>
  );
}

export default HireDraftPanel;
