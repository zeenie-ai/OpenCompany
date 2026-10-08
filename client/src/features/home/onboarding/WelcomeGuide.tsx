/**
 * The Welcome guide (onboarding handoff A): three steps over Home, Welcome,
 * Connect an AI model and Your first hire, in the Settings dialog's shape
 * (left nav, the page, a footer bar).
 *
 * It opens by itself the first time Home shows for an owner who has not
 * finished it, and again from the header's Guide button or Settings > Help
 * (useOnboarding). The nav reaches any step already visited, and Connect
 * always. A step is ticked once passed; Connect is ticked as soon as any AI
 * model is connected. Skip for now, the close button, Esc and a click
 * outside finish the guide; the last step finishes it by creating a setup.
 * Esc first closes what is open above the page: a disconnect confirmation
 * (its own layer), then a provider's page.
 */

import { useId, useLayoutEffect, useRef } from 'react';
import { ArrowLeft, ArrowRight, CircleCheck, KeyRound, Sparkles, UserPlus, X, type LucideIcon } from 'lucide-react';
import { Tabs as TabsPrimitive } from 'radix-ui';
import { OcLogo } from '@/components/brand/Logo';
import { staggerSettings } from '@/components/catalog/stagger';
import { ActionButton } from '@/components/ui/action-button';
import { Button } from '@/components/ui/button';
import Modal from '@/components/ui/Modal';
import { cn } from '@/lib/utils';
import { useConnectors } from '../data/connectors';
import { GUIDE_STEPS, useHomeStore, type GuideStep } from '../state/homeStore';
import { NAV_ITEM } from '../ui/nav';
import { MicroLabel } from '../ui/primitives';
import { ConnectStep } from './steps/ConnectStep';
import { FirstHireStep } from './steps/FirstHireStep';
import { WelcomeStep } from './steps/WelcomeStep';
import { useOnboarding } from './useOnboarding';

const NAV: Record<GuideStep, { title: string; icon: LucideIcon }> = {
  welcome: { title: 'Welcome', icon: Sparkles },
  connect: { title: 'Connect an AI model', icon: KeyRound },
  'first-hire': { title: 'Your first hire', icon: UserPlus },
};

export function WelcomeGuide() {
  const guide = useOnboarding();
  const { hasAi } = useConnectors();
  const setProvider = useHomeStore((s) => s.setGuideProvider);
  const navLabelId = useId();
  const bodyRef = useRef<HTMLDivElement>(null);
  const { open, step, index, furthest, provider } = guide;

  // The page's blocks rise in when the guide opens or the step changes.
  useLayoutEffect(() => {
    if (open) staggerSettings(bodyRef.current);
  }, [open, step]);

  // A provider's page carries its own way back.
  const onPage = step === 'connect' && provider !== null;
  const showNext = !onPage && (step === 'welcome' || (step === 'connect' && hasAi));
  const showLater = !onPage && step === 'connect' && !hasAi;

  return (
    <Modal
      isOpen={open}
      onClose={guide.skip}
      onEscapeKeyDown={(event) => {
        if (!onPage) return;
        event.preventDefault();
        setProvider(null);
      }}
      title="Welcome guide"
      hideHeader
      motion="spring"
      scrollableBody={false}
      maxWidth="min(var(--w-guide), calc(100vw - 3rem))"
      maxHeight="min(var(--h-guide), calc(100vh - 3rem))"
      className="rounded-panel bg-bg-panel shadow-dialog"
    >
      <TabsPrimitive.Root
        orientation="vertical"
        value={step}
        onValueChange={(next) => guide.goTo(next as GuideStep)}
        className="flex h-full min-h-0"
      >
        <div className="flex w-(--w-settings-nav) shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-border-default px-3 py-4">
          <MicroLabel className="px-2.5 pt-3.5 pb-1.5">
            <span id={navLabelId}>Get started</span>
          </MicroLabel>
          <TabsPrimitive.List aria-labelledby={navLabelId} className="flex flex-col gap-0.5">
            {GUIDE_STEPS.map((id, i) => {
              const { title, icon } = NAV[id];
              const done = i < index || (id === 'connect' && hasAi);
              const Icon = done && i !== index ? CircleCheck : icon;
              return (
                <TabsPrimitive.Trigger key={id} value={id} disabled={i > furthest && id !== 'connect'} className={NAV_ITEM}>
                  <Icon aria-hidden strokeWidth={1.75} className={cn('size-4.25', done && 'text-action-run-ink')} />
                  {title}
                </TabsPrimitive.Trigger>
              );
            })}
          </TabsPrimitive.List>
          <div className="mt-auto px-2.5 pt-3.5">
            <OcLogo size="settings" wordmark={false} />
          </div>
        </div>
        <div className="flex min-w-0 flex-1 flex-col">
          <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto">
            <TabsPrimitive.Content value="welcome" className="outline-none">
              <WelcomeStep />
            </TabsPrimitive.Content>
            <TabsPrimitive.Content value="connect" className="outline-none">
              <ConnectStep onFinish={guide.complete} />
            </TabsPrimitive.Content>
            <TabsPrimitive.Content value="first-hire" className="outline-none">
              <FirstHireStep onDone={guide.complete} onNeedsModel={() => guide.goTo('connect')} />
            </TabsPrimitive.Content>
          </div>
          <div className="flex items-center gap-2 border-t border-border-default bg-bg-panel px-6 py-3.5">
            <Button variant="quiet" onClick={guide.skip} className="h-8 px-2.5">
              Skip for now
            </Button>
            <span className="ml-auto font-mono text-2xs text-fg-faint">
              {index + 1} / {GUIDE_STEPS.length}
            </span>
            {guide.back && !onPage && (
              <Button variant="quiet" onClick={guide.back} className="h-8 gap-1.5 border-border-strong px-3 font-semibold text-fg-default">
                <ArrowLeft aria-hidden />
                Back
              </Button>
            )}
            {showNext && guide.next && (
              <ActionButton intent="tools" onClick={guide.next}>
                Next
                <ArrowRight aria-hidden className="size-4" />
              </ActionButton>
            )}
            {showLater && guide.next && (
              <Button variant="quiet" onClick={guide.next} className="h-8 px-3 font-semibold">
                I’ll do this later
              </Button>
            )}
          </div>
        </div>
      </TabsPrimitive.Root>
      <Button variant="quiet" size="icon" onClick={guide.skip} aria-label="Close guide" className="absolute top-3.5 right-3.5 z-10">
        <X />
      </Button>
    </Modal>
  );
}

export default WelcomeGuide;
