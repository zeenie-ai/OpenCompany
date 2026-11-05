/**
 * The header's Get started pill (onboarding v3): on an employee's page the
 * checklist card would cover Send, so the checklist shows there as "Get
 * started · n/4", under the card's own rule (useGetStarted). It goes back
 * to the hire view with the card open.
 */

import { ListChecks } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useHomeStore } from '../state/homeStore';
import { useGetStarted } from './useGetStarted';

export function GetStartedPill() {
  const openGetStarted = useHomeStore((s) => s.openGetStarted);
  const { visible, rows, doneCount } = useGetStarted();
  if (!visible) return null;
  return (
    <Button
      variant="quiet"
      title="Get started: your first steps, ticked off as you go"
      onClick={openGetStarted}
      className="h-8.5 rounded-pill border-border-default bg-bg-panel px-3 text-fg-default"
    >
      <ListChecks aria-hidden className="size-3.75" strokeWidth={1.75} />
      Get started · {doneCount}/{rows.length}
    </Button>
  );
}

export default GetStartedPill;
