/**
 * The screen the sign-in gate shows before the app (onboarding handoff R4,
 * R5): the whole window in the app's background, the orb filling it, and a
 * top bar with the logo and the theme button. Connecting and sign-in render
 * inside one, so the orb glides from one's slot to the other's. As the
 * overlay (signed in, the server away) it covers the app, which stays
 * mounted underneath and inert, and borrows the orb until it goes. While
 * one shows, the theme is the base of the chosen family, as on Home.
 */

import { useLayoutEffect, type ReactNode } from 'react';
import { OcLogo } from '@/components/brand/Logo';
import { ThemeButton } from '@/features/home/header/ThemeButton';
import { OrbStage } from '@/features/home/orb/OrbStage';
import { cn } from '@/lib/utils';
import { useShellDialogsStore } from '@/stores/shellDialogsStore';

export function ConnectScreen({ overlay = false, children }: { overlay?: boolean; children: ReactNode }) {
  useLayoutEffect(() => {
    const { setConnectScreenOpen } = useShellDialogsStore.getState();
    setConnectScreenOpen(true);
    return () => setConnectScreenOpen(false);
  }, []);

  return (
    <div
      role={overlay ? 'dialog' : undefined}
      aria-modal={overlay ? true : undefined}
      aria-label={overlay ? 'Reconnecting to OpenCompany' : undefined}
      className={cn('fixed inset-0 overflow-y-auto bg-bg-app text-fg-default', overlay && 'z-60')}
    >
      <OrbStage fill="screen" />
      <div className="absolute inset-x-0 top-0 z-10 flex items-center justify-between px-5 py-4">
        <OcLogo size="header" />
        <ThemeButton />
      </div>
      <div className="relative z-1 flex min-h-full flex-col items-center justify-center px-6 pt-18 pb-10">{children}</div>
    </div>
  );
}

export default ConnectScreen;
