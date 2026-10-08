/**
 * Connecting (onboarding handoff R4): while the server can't be reached,
 * the orb waits and a pill counts down to the next check, with the attempt
 * and Try now (useRetrySchedule); from the third attempt a line says to
 * make sure OpenCompany is running. Once the server answers, a green
 * "Connected" pops in and the orb spikes; the gate moves on a moment later.
 *
 * The countdown is not read out every second: one status line for screen
 * readers says what changed (another attempt, the server found, connected).
 * `again` is the overlay's case: the app was running and lost the server.
 */

import { Check } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { RingSpinner } from '@/components/ui/ring-spinner';
import { SPIKE, setOrbWaiting, spikeOrb } from '@/features/home/orb/orb';
import { OrbSlot } from '@/features/home/orb/OrbSlot';
import { CONNECT_RETRY } from '@/lib/connectionConfig';
import { animate, rise } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { useRetrySchedule } from './useRetrySchedule';

function ConnectedPill() {
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    animate(ref.current, [{ opacity: 0, transform: 'scale(0.92)' }, { opacity: 1, transform: 'none' }], {
      duration: 'chip-pop',
      easing: 'overshoot',
    });
  }, []);
  return (
    <span
      ref={ref}
      className="mt-2 inline-flex h-10 items-center gap-2 rounded-pill border border-status-working-border bg-status-working-fill px-4 text-row font-semibold text-status-working-ink shadow-glow-connect"
    >
      <Check aria-hidden className="size-3.75" strokeWidth={2.5} />
      Connected
    </span>
  );
}

export function ConnectingPanel({
  check,
  connected,
  again = false,
}: {
  /** Checks the server once; true when it answered. */
  check: () => Promise<boolean>;
  /** The gate has what it waited for. */
  connected: boolean;
  again?: boolean;
}) {
  const { attempt, wait, checking, found, tryNow } = useRetrySchedule(check);
  const eyebrowRef = useRef<HTMLParagraphElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const pillRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    rise(eyebrowRef.current);
    rise(titleRef.current, { delay: 80 });
    rise(pillRef.current, { delay: 240 });
  }, []);

  useEffect(() => {
    setOrbWaiting(!connected);
    if (connected) spikeOrb(SPIKE.connected);
  }, [connected]);
  useEffect(() => () => setOrbWaiting(false), []);

  const reached = connected || found;
  const eyebrow = reached ? 'Server found' : again || attempt > 1 ? 'Reconnecting' : 'Connecting';
  const countdown = checking || found ? 'Connecting…' : `Trying again in ${wait}s`;
  const help = !connected && attempt >= CONNECT_RETRY.HELP_FROM_ATTEMPT;
  const status = connected
    ? 'Connected'
    : found
      ? 'Found the OpenCompany server. Connecting.'
      : `Can’t reach OpenCompany yet. Attempt ${attempt}.`;

  return (
    <div className="flex w-full max-w-130 flex-col items-center gap-3.5 text-center">
      <OrbSlot size="connecting" />
      <p ref={eyebrowRef} className="m-0 flex items-center gap-2 font-mono text-xs font-medium tracking-label text-fg-muted uppercase">
        <span
          aria-hidden
          className={cn(
            'size-1.5 rounded-full bg-current shadow-pip',
            reached ? 'text-status-working-dot' : 'opencompany-pip-pulse text-status-waiting-dot',
          )}
        />
        {eyebrow}
      </p>
      <h1 ref={titleRef} className="m-0 text-2xl leading-hero font-semibold tracking-hero text-balance text-fg-default">
        {connected ? 'Connected' : 'Waking up OpenCompany'}
      </h1>
      {connected ? (
        <ConnectedPill />
      ) : (
        <div
          ref={pillRef}
          className="mt-2 inline-flex h-10 items-center gap-2.5 rounded-pill border border-border-default bg-bg-panel pr-1.5 pl-3.5 shadow-float"
        >
          <RingSpinner className="size-3 border-status-waiting-border border-t-status-waiting-dot" />
          <span aria-hidden className="min-w-33 text-left font-mono text-xs text-fg-default">
            {countdown}
          </span>
          <span aria-hidden className="h-4 w-px bg-border-default" />
          <span className="font-mono text-2xs text-fg-faint">Attempt {attempt}</span>
          <Button
            variant="quiet"
            onClick={tryNow}
            disabled={checking || found}
            className="h-7 rounded-pill border-border-default bg-bg-elevated px-3 text-meta font-semibold text-fg-default"
          >
            Try now
          </Button>
        </div>
      )}
      {help && <p className="m-0 mt-1.5 text-sm text-fg-muted">Still nothing? Make sure OpenCompany is running.</p>}
      <p role="status" className="sr-only">
        {status}
      </p>
    </div>
  );
}

export default ConnectingPanel;
