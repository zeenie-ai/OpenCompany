/**
 * Signed in (onboarding handoff R5): in place of the sign-in card, who they
 * are and that their team is opening. It hands over the way a mode switch
 * does (app/useShellActions): the screen the app opens on loads while this
 * rises in, then `finishSignIn` ends the sign-in and the gate shows the app.
 * The orb spikes as it shows.
 */

import { useLayoutEffect, useRef } from 'react';
import { RingSpinner } from '@/components/ui/ring-spinner';
import { useAuth } from '@/contexts/AuthContext';
import { firstName } from '@/features/home/data/profile';
import { SPIKE, spikeOrb } from '@/features/home/orb/orb';
import { finished, rise } from '@/lib/motion';
import { preloadEditor, preloadHome, useShellMode } from '../../app/ShellModeSwitch';

export function SignedIn({ created, name }: { created: boolean; name: string }) {
  const { finishSignIn } = useAuth();
  const mode = useShellMode();
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    spikeOrb(SPIKE.signedIn);
    let current = true;
    const screen = (mode === 'normal' ? preloadHome() : preloadEditor()).catch((error: unknown) => {
      // The screen's own error boundary says so once it shows.
      console.error('[Shell] Could not load the screen:', error);
    });
    void Promise.all([finished(rise(ref.current)), screen]).then(() => {
      if (current) finishSignIn();
    });
    return () => {
      current = false;
    };
  }, [mode, finishSignIn]);

  const first = firstName(name);
  const greeting = created ? 'Welcome' : 'Welcome back';
  return (
    <div ref={ref} className="flex flex-col items-center gap-2.5 text-center">
      <p className="m-0 font-mono text-2xs font-medium tracking-label text-node-agent-ink uppercase">
        {created ? 'Account created' : 'Signed in'}
      </p>
      <h1 className="m-0 text-headline leading-hero font-semibold tracking-hero text-balance text-fg-default">
        {first ? `${greeting}, ${first}` : greeting}
      </h1>
      <p role="status" className="m-0 flex items-center gap-2 text-base text-fg-muted">
        <RingSpinner className="size-3 border-border-default border-t-fg-default" />
        Opening your team…
      </p>
    </div>
  );
}

export default SignedIn;
