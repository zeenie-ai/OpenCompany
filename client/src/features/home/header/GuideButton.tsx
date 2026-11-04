/**
 * The header's Guide pill: opens the Welcome guide again at its first step.
 * Styled as the Workspace pill at rest.
 */

import { CircleHelp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useHomeStore } from '../state/homeStore';

export function GuideButton() {
  const openGuide = useHomeStore((s) => s.openGuide);
  return (
    <Button
      variant="quiet"
      title="Welcome guide: connect an AI model and hire your first employee"
      onClick={() => openGuide('welcome')}
      className="h-8.5 gap-1.75 rounded-pill border-border-default px-3 text-sm text-fg-default hover:border-border-strong"
    >
      <CircleHelp aria-hidden className="size-3.75" strokeWidth={1.75} />
      Guide
    </Button>
  );
}

export default GuideButton;
