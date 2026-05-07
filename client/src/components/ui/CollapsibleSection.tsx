import React from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';

interface CollapsibleSectionProps {
  title: string | React.ReactNode;
  isCollapsed: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

const CollapsibleSection: React.FC<CollapsibleSectionProps> = ({
  title,
  isCollapsed,
  onToggle,
  children,
}) => {
  const open = !isCollapsed;
  return (
    <Collapsible open={open} onOpenChange={() => onToggle()}>
      {/* bg-bg-app + border-default for the outer card; bg-bg-elevated
          on the trigger so the head sits above the body — matches the
          handoff `.cat` / `.cat-head` two-tier surface. `cat` /
          `cat-head` co-classes activate per-theme decorations. */}
      <div className={cn('cat overflow-hidden rounded-lg border border-border-default bg-bg-app', isCollapsed && 'collapsed')}>
        <CollapsibleTrigger className="cat-head flex w-full cursor-pointer items-center justify-between gap-2 border-none bg-bg-elevated px-4 py-3 text-base text-fg-default transition-colors hover:bg-bg-hover">
          {typeof title === 'string' ? (
            <span className="font-display font-medium">{title}</span>
          ) : (
            <div className="flex flex-1 items-center">{title}</div>
          )}
          <ChevronDown
            className={cn(
              'h-3 w-3 shrink-0 text-fg-muted transition-transform',
              isCollapsed && '-rotate-90'
            )}
          />
        </CollapsibleTrigger>

        <CollapsibleContent className={cn('cat-body transition-[padding]', open && 'p-3')}>
          {children}
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
};

export default CollapsibleSection;
