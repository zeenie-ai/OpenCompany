/**
 * Slash commands (design handoff chat, "Composer": `/`). A draft that is a
 * single word starting with `/` opens a list of the commands it begins,
 * above the message box; ArrowUp and ArrowDown move, Enter or Tab puts the
 * command's text in the box, Esc closes it (until the draft changes). Focus
 * stays in the box: the list is the popup it controls (`aria-controls`), and
 * the highlighted option is its `aria-activedescendant`. cmdk names its list
 * and options itself, so both ids are read from the list once it has drawn.
 * The list is placed against the box through a ref (`anchorRef`): it has no
 * button of its own that could claim the anchor, and the box itself may be
 * the model picker's anchor (composer/ModelPicker.tsx).
 */

import { useLayoutEffect, type RefObject } from 'react';
import { Command, CommandItem, CommandList } from '@/components/ui/command';
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover';
import type { ChatCommand } from '../data/chatContext';

export function SlashMenu({
  open,
  listId,
  items,
  active,
  boxRef,
  anchorRef,
  onPick,
  onActive,
  onDismiss,
}: {
  open: boolean;
  listId: string;
  items: readonly ChatCommand[];
  /** The highlighted command's index. */
  active: number;
  /** The text box: the combobox whose popup this is. */
  boxRef: RefObject<HTMLTextAreaElement | null>;
  /** The message box (its border) the list opens above. */
  anchorRef: RefObject<HTMLDivElement | null>;
  onPick: (command: ChatCommand) => void;
  onActive: (index: number) => void;
  onDismiss: () => void;
}) {
  const shown = open && items.length > 0;
  const selected = items[active]?.command ?? '';

  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const holder = shown ? document.getElementById(listId) : null;
    const list = holder?.querySelector<HTMLElement>('[cmdk-list]');
    // By position: cmdk marks its selection a render later than this.
    const option = holder?.querySelectorAll<HTMLElement>('[cmdk-item]')[active];
    if (list?.id) box.setAttribute('aria-controls', list.id);
    else box.removeAttribute('aria-controls');
    if (option?.id) box.setAttribute('aria-activedescendant', option.id);
    else box.removeAttribute('aria-activedescendant');
  });

  return (
    <Popover open={shown} onOpenChange={(next) => !next && onDismiss()}>
      <PopoverAnchor virtualRef={anchorRef} />
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        onOpenAutoFocus={(event) => event.preventDefault()}
        onCloseAutoFocus={(event) => event.preventDefault()}
        className="w-(--radix-popover-trigger-width) max-w-105 p-1"
      >
        <Command id={listId} shouldFilter={false} value={selected} label="Commands">
          <CommandList label="Commands">
            {items.map((item, index) => (
              <CommandItem
                key={item.command}
                value={item.command}
                onSelect={() => onPick(item)}
                onMouseEnter={() => onActive(index)}
                className="flex-col items-start gap-0.5"
              >
                <span className="font-mono text-sm font-medium text-fg-default">{item.command}</span>
                {item.description && <span className="text-xs text-fg-muted">{item.description}</span>}
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
