/**
 * The model menu, the one list both model pickers show: Home's chat picker
 * (features/chat/composer/ModelPicker.tsx) and the editor's Set Global Model
 * (components/ui/TopToolbar.tsx). Each connected provider's models sit under
 * the provider's name, the chosen one ticked, and nothing is coloured:
 * colour marks only state or role (docs-internal/theme_system.md), so a
 * provider gets no brand colour. ArrowUp and ArrowDown move, Enter picks
 * (cmdk). A picker opens it from its own button in `ModelMenuContent`, the
 * shared panel, and gives the list the focus when the panel opens
 * (`listRef`).
 */

import { Check } from 'lucide-react';
import { useState, type ComponentProps, type KeyboardEvent, type Ref } from 'react';
import { Command, CommandGroup, CommandItem, CommandList, CommandSeparator } from '@/components/ui/command';
import { PopoverContent } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

export interface ModelMenuItem {
  /** What a pick returns: `provider::model`, or `auto`. */
  value: string;
  label: string;
  /** A second line: what the row uses, or why it can't be picked. */
  line?: string | null;
  disabled?: boolean;
}

export interface ModelMenuGroup {
  key: string;
  /** The provider's name. */
  heading: string;
  items: readonly ModelMenuItem[];
}

/** The panel the menu opens in, above or below its picker's button. */
export function ModelMenuContent({ className, ...props }: ComponentProps<typeof PopoverContent>) {
  return (
    <PopoverContent
      className={cn(
        'flex max-h-[min(var(--h-chat-model-menu),calc(100vh-160px))] w-(--w-chat-model-menu) flex-col gap-1 rounded-xl border border-border-default bg-bg-elevated p-1.5 shadow-card-hover ring-0 duration-(--dur-popover-in) ease-spring',
        className,
      )}
      {...props}
    />
  );
}

export function ModelMenu({
  label,
  lead = [],
  groups,
  value,
  onSelect,
  onKeyDown,
  listRef,
}: {
  /** The list's accessible name. */
  label: string;
  /** Rows before the providers' (Home's Auto), with a rule under them. */
  lead?: readonly ModelMenuItem[];
  groups: readonly ModelMenuGroup[];
  /** The chosen row's value. */
  value: string | undefined;
  onSelect: (value: string) => void;
  onKeyDown?: (event: KeyboardEvent<HTMLDivElement>) => void;
  listRef?: Ref<HTMLDivElement>;
}) {
  const shown = groups.filter((group) => group.items.length > 0);
  // The menu opens on the chosen row, else on the first the owner can pick.
  const [highlight, setHighlight] = useState(() => {
    const rows = [...lead, ...shown.flatMap((group) => group.items)];
    const chosen = rows.find((row) => row.value === value && !row.disabled);
    return chosen?.value ?? rows.find((row) => !row.disabled)?.value ?? '';
  });
  const row = (item: ModelMenuItem) => (
    <ModelRow key={item.value} item={item} chosen={item.value === value} onSelect={onSelect} />
  );

  return (
    <Command
      value={highlight}
      onValueChange={setHighlight}
      shouldFilter={false}
      loop
      label={label}
      onKeyDown={onKeyDown}
      className="min-h-0 flex-1 bg-transparent"
    >
      <CommandList ref={listRef} label={label} className="max-h-none flex-1 outline-none [scrollbar-gutter:stable]">
        {lead.map(row)}
        {lead.length > 0 && shown.length > 0 && <CommandSeparator className="mx-1.5 my-1" />}
        {shown.map((group) => (
          <CommandGroup key={group.key} heading={group.heading}>
            {group.items.map(row)}
          </CommandGroup>
        ))}
      </CommandList>
    </Command>
  );
}

function ModelRow({ item, chosen, onSelect }: { item: ModelMenuItem; chosen: boolean; onSelect: (value: string) => void }) {
  return (
    <CommandItem
      value={item.value}
      disabled={item.disabled}
      onSelect={() => onSelect(item.value)}
      className="min-h-9 gap-2.5 rounded-lg px-2.5 py-1.5 text-fg-default transition-colors duration-(--dur-fast)"
    >
      <span className="flex min-w-0 flex-1 flex-col gap-px">
        <span className="truncate text-sm font-medium" title={item.label}>
          {item.label}
        </span>
        {item.line && <span className="text-xs text-fg-muted">{item.line}</span>}
      </span>
      {chosen && <Check aria-hidden strokeWidth={2.2} className="size-4 text-fg-default" />}
    </CommandItem>
  );
}
