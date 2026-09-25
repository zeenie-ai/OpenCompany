/**
 * The app shell: everything that belongs to the app rather than to one
 * screen. It owns the decorative `.app-frame` (per-theme outer ornaments),
 * the boot-time effects both screens rely on, the app-level dialogs, and the
 * switch between Normal mode (Home) and Dev mode (the workflow editor).
 *
 * The effects live in <ShellEffects/> and the dialogs in <ShellDialogs/> so
 * their re-renders (a theme change, a dialog opening) never re-render the
 * screen. Nothing here subscribes to the main WebSocket context, whose value
 * changes on every console, chat and terminal line.
 */

import { Suspense, lazy, useState } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useSoundSync } from '../hooks/useSound';
import { useSaveUserSettingsMutation } from '../hooks/useUserSettingsQuery';
import { useShellDialogsStore } from '../stores/shellDialogsStore';
import { useWorkflowSettingsStore } from '../stores/workflowSettingsStore';
import { ShellModeSwitch } from './ShellModeSwitch';
import { useCurrentWorkflowSync } from './useCurrentWorkflowSync';
import { useModeShortcut } from './useModeShortcut';
import { usePageActivitySync } from './usePageActivitySync';
import { useUIDefaultsOnce } from './useUIDefaultsOnce';

const SettingsPanel = lazy(() => import('../components/ui/SettingsPanel'));
const CredentialsModal = lazy(() => import('../components/CredentialsModal'));

function ShellEffects() {
  // Mirror the sound preference into the WebAudio engine and re-read
  // --sound-pack whenever the theme changes.
  useSoundSync();
  usePageActivitySync();
  useCurrentWorkflowSync();
  useUIDefaultsOnce();
  useModeShortcut();
  return null;
}

/** Latches true the first time `open` is true, so a dialog's chunk loads
 *  only when it is first opened and it stays mounted for its close
 *  animation afterwards. */
function useOpenedOnce(open: boolean): boolean {
  const [opened, setOpened] = useState(open);
  if (open && !opened) setOpened(true);
  return opened || open;
}

function ShellDialogs() {
  const settingsOpen = useShellDialogsStore((s) => s.settingsOpen);
  const credentialsOpen = useShellDialogsStore((s) => s.credentialsOpen);
  const closeSettings = useShellDialogsStore((s) => s.closeSettings);
  const closeCredentials = useShellDialogsStore((s) => s.closeCredentials);
  const replayOnboarding = useShellDialogsStore((s) => s.replayOnboarding);
  const settings = useWorkflowSettingsStore((s) => s.settings);
  const setSettings = useWorkflowSettingsStore((s) => s.setSettings);
  const saveUserSettings = useSaveUserSettingsMutation();
  const settingsMounted = useOpenedOnce(settingsOpen);
  const credentialsMounted = useOpenedOnce(credentialsOpen);

  return (
    <Suspense fallback={null}>
      {settingsMounted && (
        <SettingsPanel
          isOpen={settingsOpen}
          onClose={closeSettings}
          settings={settings}
          onSettingsChange={setSettings}
          onReplayOnboarding={replayOnboarding}
          onShowGetStarted={() => {
            saveUserSettings.mutate({ getting_started_dismissed: false });
            closeSettings();
          }}
        />
      )}
      {credentialsMounted && <CredentialsModal visible={credentialsOpen} onClose={closeCredentials} />}
    </Suspense>
  );
}

export default function AppShell() {
  return (
    <TooltipProvider>
      <ShellEffects />
      {/* `app-frame` is the decorative-layer hook from the design handoff:
          per-theme CSS targets it for outer ornaments (gilded corners under
          Renaissance, scanlines and corner brackets under Cyber, the riveted
          frame under Steampunk, the REC dot under Surveillance). The
          decorations are pointer-events: none. */}
      <div className="app-frame flex h-screen w-screen flex-col bg-bg-app font-body">
        <ShellModeSwitch />
      </div>
      <ShellDialogs />
    </TooltipProvider>
  );
}
