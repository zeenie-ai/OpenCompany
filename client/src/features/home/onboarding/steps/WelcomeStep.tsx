/**
 * Guide step 1 (onboarding handoff A1, trimmed by R1): who the guide is for,
 * one line on what OpenCompany does, and the three-beat demo.
 */

import { callName, useOwnerSettings } from '../../data/profile';
import { MicroLabel } from '../../ui/primitives';
import { WelcomeDemo } from './WelcomeDemo';

export function WelcomeStep() {
  const { data: settings } = useOwnerSettings();
  const name = callName(settings);
  return (
    <div className="flex flex-col gap-6 px-8 pt-7.5 pb-6.5">
      <div data-stagger className="flex flex-col gap-2.5 pr-10">
        <MicroLabel className="text-node-agent-ink">{name ? `Welcome, ${name}` : 'Welcome'}</MicroLabel>
        <h2 className="m-0 text-headline leading-hero font-semibold tracking-hero text-balance text-fg-default">
          Your AI team, hired in plain words.
        </h2>
      </div>
      <WelcomeDemo name={name} />
    </div>
  );
}

export default WelcomeStep;
