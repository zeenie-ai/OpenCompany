import React from 'react';
import { ArrowLeft, ArrowRight, Check, MessageCircle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ActionButton } from '@/components/ui/action-button';
import { cn } from '@/lib/utils';
import Modal from '../ui/Modal';
import { useOnboarding } from '../../hooks/useOnboarding';
import WelcomeStep from './steps/WelcomeStep';
import HowItWorksStep from './steps/HowItWorksStep';
import ConnectAIStep from './steps/ConnectAIStep';
import TryItStep from './steps/TryItStep';

interface OnboardingWizardProps {
  onOpenCredentials: () => void;
  reopenTrigger?: number;
  /** Called after the final step's button completes the wizard — the
   *  Dashboard uses it to open the AI Assistant example and focus chat.
   *  Never called on skip or modal close. */
  onFinish?: () => void;
}

// Single source of truth for the wizard's step list. Length feeds the
// hook's totalSteps and the progress indicator; renderer is dispatched
// by index. Adding a step is a one-line edit here.
const STEPS: { title: string; render: (props: { onOpenCredentials: () => void }) => React.ReactNode }[] = [
  { title: 'Welcome',         render: () => <WelcomeStep /> },
  { title: 'How it works',    render: () => <HowItWorksStep /> },
  { title: 'Connect your AI', render: ({ onOpenCredentials }) => <ConnectAIStep onOpenCredentials={onOpenCredentials} /> },
  { title: 'Try it',          render: () => <TryItStep /> },
];

const OnboardingWizard: React.FC<OnboardingWizardProps> = ({ onOpenCredentials, reopenTrigger, onFinish }) => {
  const {
    isVisible,
    currentStep,
    isLoading,
    hasChecked,
    nextStep,
    prevStep,
    skip,
    complete,
  } = useOnboarding(reopenTrigger, STEPS.length);

  if (!isVisible || !hasChecked || isLoading) return null;

  const safeIndex = Math.min(Math.max(currentStep, 0), STEPS.length - 1);
  const isLastStep = safeIndex === STEPS.length - 1;

  const handleFinish = () => {
    complete();
    onFinish?.();
  };

  return (
    <Modal
      isOpen={isVisible}
      onClose={skip}
      title="Welcome Guide"
      maxWidth="95vw"
      maxHeight="95vh"
    >
      <div className="flex flex-col px-5 pt-4 pb-3">
        {/* Progress steps */}
        <ol className="mb-4 flex w-full items-center">
          {STEPS.map((item, idx) => {
            const status: 'completed' | 'active' | 'upcoming' =
              idx < safeIndex ? 'completed' : idx === safeIndex ? 'active' : 'upcoming';
            const isLast = idx === STEPS.length - 1;
            return (
              <li key={item.title} className="flex flex-1 items-center gap-2">
                <div
                  className={cn(
                    'flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium transition-colors',
                    status === 'completed' && 'border-primary bg-primary text-primary-foreground',
                    status === 'active' && 'border-primary text-primary',
                    status === 'upcoming' && 'border-border text-muted-foreground',
                  )}
                >
                  {status === 'completed' ? <Check className="h-4 w-4" /> : idx + 1}
                </div>
                <span
                  className={cn(
                    'whitespace-nowrap text-xs',
                    status === 'upcoming' ? 'text-muted-foreground' : 'text-foreground',
                  )}
                >
                  {item.title}
                </span>
                {!isLast && (
                  <div
                    className={cn(
                      'h-px flex-1',
                      idx < safeIndex ? 'bg-primary' : 'bg-border',
                    )}
                  />
                )}
              </li>
            );
          })}
        </ol>

        {/* Step content */}
        <div className="overflow-y-auto pr-1 max-h-[calc(95vh-200px)]">
          {STEPS[safeIndex].render({ onOpenCredentials })}
        </div>

        {/* Footer navigation */}
        <div className="flex items-center justify-between border-t border-border pt-3 mt-3">
          <Button variant="ghost" size="sm" onClick={skip} className="text-muted-foreground">
            Skip for now
          </Button>

          <div className="flex items-center gap-2">
            {safeIndex > 0 && (
              <Button variant="outline" onClick={prevStep}>
                <ArrowLeft className="h-4 w-4" />
                Back
              </Button>
            )}
            {isLastStep ? (
              <ActionButton intent="run" onClick={handleFinish}>
                <MessageCircle className="h-4 w-4" />
                Open AI Assistant
              </ActionButton>
            ) : (
              <ActionButton intent="tools" onClick={nextStep}>
                Next
                <ArrowRight className="h-4 w-4" />
              </ActionButton>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
};

export default OnboardingWizard;
