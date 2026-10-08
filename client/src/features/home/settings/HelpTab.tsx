/**
 * Settings > Help: the way back to the Welcome guide, and to the Get
 * started checklist once it is hidden.
 */

import { Button } from '@/components/ui/button';
import { useShowGetStarted } from '../onboarding/useGetStarted';
import { useHomeStore } from '../state/homeStore';
import { SettingRow } from './SettingRow';

const ROW_BUTTON = 'h-8 border-border-strong px-3 text-sm font-semibold text-fg-default';

export function HelpTab({ onDone }: { onDone: () => void }) {
  const openGuide = useHomeStore((s) => s.openGuide);
  const showGetStarted = useShowGetStarted();
  return (
    <div className="flex max-w-170 flex-col gap-5 px-8 pt-7 pb-8">
      <div data-stagger className="flex flex-col gap-1 pr-9">
        <h2 className="text-lg font-semibold tracking-[-0.01em] text-fg-default">Help</h2>
        <p className="text-sm text-pretty text-fg-muted">Find your way around OpenCompany again.</p>
      </div>
      <div data-stagger className="flex flex-col">
        <SettingRow title="Welcome guide" detail="Connect an AI model and hire your first employee, step by step.">
          <Button
            variant="quiet"
            onClick={() => {
              onDone();
              openGuide('welcome');
            }}
            className={ROW_BUTTON}
          >
            Replay
          </Button>
        </SettingRow>
        <SettingRow title="Get started checklist" detail="Your first steps, ticked off as you go, in the corner of Home." last>
          <Button
            variant="quiet"
            onClick={() => {
              onDone();
              showGetStarted();
            }}
            className={ROW_BUTTON}
          >
            Show
          </Button>
        </SettingRow>
      </div>
    </div>
  );
}

export default HelpTab;
