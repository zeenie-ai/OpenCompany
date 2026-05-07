import React, { useState } from 'react';
import { GripVertical } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Card } from '@/components/ui/card';
import { INodeTypeDescription } from '../../types/INodeProperties';
import { NodeIcon } from '../../assets/icons';
import { useNodeSpec } from '../../lib/nodeSpec';

interface ComponentItemProps {
  definition: INodeTypeDescription;
  onDragStart: (event: React.DragEvent, definition: INodeTypeDescription) => void;
}

const ComponentItem: React.FC<ComponentItemProps> = ({ definition: localDefinition, onDragStart }) => {
  const [isDragging, setIsDragging] = useState(false);

  // Subscribe to the backend NodeSpec cache so the palette icon
  // populates the moment prefetch lands. Icon ref comes from the spec;
  // display fields fall back to the bundled definition.
  const spec = useNodeSpec(localDefinition.name);
  const definition = localDefinition;
  const iconRaw = spec?.icon ?? definition.icon;

  return (
    // bg-bg-app + border-default match the handoff `.comp` card.
    // Hover lifts via translate + outline + soft tint backdrop.
    <Card
      size="sm"
      draggable
      onDragStart={(e) => {
        setIsDragging(true);
        onDragStart(e, localDefinition);
      }}
      onDragEnd={() => setIsDragging(false)}
      className={cn(
        'group relative flex-row items-center gap-3 px-3 py-2 cursor-grab select-none',
        'border-border-default bg-bg-app transition-all duration-150 ease-out',
        'hover:-translate-y-0.5 hover:bg-bg-hover hover:ring-2 hover:ring-foreground/15 hover:shadow-md',
        isDragging && 'opacity-50',
      )}
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-bg-elevated ring-1 ring-border-default/40">
        <NodeIcon
          icon={iconRaw}
          className="h-5 w-5 text-lg"
          fallback={<span>📦</span>}
        />
      </div>

      <div className="min-w-0 flex-1">
        <div className="truncate font-display text-sm font-medium text-fg-default">
          {definition.displayName}
        </div>
        <div className="truncate text-xs leading-tight text-fg-muted">
          {definition.description}
        </div>
      </div>

      <GripVertical
        className="h-4 w-4 shrink-0 text-fg-faint opacity-50 transition-opacity group-hover:opacity-80"
        aria-hidden
      />
    </Card>
  );
};

export default ComponentItem;
