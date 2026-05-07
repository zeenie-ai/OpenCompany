/**
 * CommandPalette — global ⌘K (Ctrl+K) command launcher.
 *
 * Lightweight shell action surface inspired by the design handoff's
 * `.cmdk` panel. Keeps a small registered command set in state; the
 * Dashboard wires the actual handlers (`onOpenSettings`, etc.) via the
 * `commands` prop. Composes on top of the cmdk library that ships with
 * the codebase (CredentialsPalette uses the same dependency).
 *
 * Token-driven: chrome reads bg-bg-elevated + border-border-strong;
 * active row reads bg-bg-active + text-accent. Under Renaissance the
 * panel becomes an "open scroll" via the per-theme background image
 * (declared in renaissance.css `.cmdk` block — wired through here by
 * applying the `cmdk` class on the outer wrapper). Under Cyber it
 * becomes a "root terminal" with neon-cyan border + scanlines.
 */

import * as React from 'react';
import { useEffect } from 'react';
import { Command } from 'cmdk';
import { Search } from 'lucide-react';
import { Dialog, DialogPortal, DialogOverlay, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Dialog as DialogPrimitive } from 'radix-ui';
import { cn } from '@/lib/utils';

export interface CommandItem {
  id: string;
  label: string;
  hint?: string;
  shortcut?: string;
  /** lucide-react icon component */
  icon?: React.ComponentType<{ className?: string }>;
  /** Group label used to bucket items in the list. */
  group?: string;
  /** Invoked when the user presses Enter / clicks the row. */
  onRun: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  commands: CommandItem[];
}

export const CommandPalette: React.FC<CommandPaletteProps> = ({ open, onOpenChange, commands }) => {
  // ⌘K / Ctrl+K toggles the palette globally.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        onOpenChange(!open);
      } else if (e.key === 'Escape' && open) {
        onOpenChange(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onOpenChange]);

  // Bucket commands by group so the list renders Command.Group sections.
  const groups = React.useMemo(() => {
    const buckets: Record<string, CommandItem[]> = {};
    for (const cmd of commands) {
      const key = cmd.group ?? 'Actions';
      (buckets[key] ??= []).push(cmd);
    }
    return buckets;
  }, [commands]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay className="bg-bg-overlay" />
        <DialogPrimitive.Content
          data-slot="command-palette"
          className={cn(
            // `cmdk` class is the per-theme decorative hook (renaissance
            // and cyber CSS apply backgrounds + borders + scanlines via
            // `:root[data-theme="..."] .cmdk`).
            'cmdk fixed left-1/2 top-[14%] z-50 w-[min(560px,92vw)] -translate-x-1/2 overflow-hidden rounded-md border border-border-strong bg-bg-elevated shadow-2xl outline-none',
            'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 duration-100',
          )}
        >
          <DialogTitle className="sr-only">Command palette</DialogTitle>
          <DialogDescription className="sr-only">Run a command from the keyboard</DialogDescription>

          <Command label="Command palette" className="flex flex-col" shouldFilter>
            <div className="relative border-b border-border-default px-4">
              <Search className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
              <Command.Input
                placeholder="Type a command or search..."
                autoFocus
                className="h-12 w-full bg-transparent pl-7 font-display text-[15px] tracking-[var(--type-tracking-display)] text-fg-default placeholder:text-fg-faint focus:outline-none [text-transform:var(--type-uppercase)]"
              />
            </div>

            <Command.List className="max-h-[360px] overflow-y-auto p-1.5">
              <Command.Empty className="px-3 py-6 text-center text-sm text-fg-muted">
                No matching commands
              </Command.Empty>

              {Object.entries(groups).map(([groupName, items]) => (
                <Command.Group
                  key={groupName}
                  heading={groupName}
                  className="px-1 py-1 text-[10px] font-semibold uppercase tracking-wider text-fg-faint"
                >
                  {items.map((cmd) => {
                    const Icon = cmd.icon;
                    return (
                      <Command.Item
                        key={cmd.id}
                        value={`${cmd.label} ${cmd.hint ?? ''}`}
                        onSelect={() => {
                          onOpenChange(false);
                          // Defer the action one frame so the dialog
                          // closes cleanly before the handler fires
                          // (avoids focus-trap clashes when the action
                          // opens another modal).
                          requestAnimationFrame(() => cmd.onRun());
                        }}
                        className="flex cursor-pointer items-center gap-3 rounded-sm px-3 py-2 text-sm text-fg-default data-[selected=true]:bg-bg-active data-[selected=true]:text-accent"
                      >
                        {Icon && <Icon className="h-4 w-4 shrink-0" />}
                        <span className="flex-1 truncate font-display">{cmd.label}</span>
                        {cmd.hint && (
                          <span className="font-mono text-[11px] text-fg-faint">{cmd.hint}</span>
                        )}
                        {cmd.shortcut && (
                          <kbd className="ml-auto rounded-sm border border-border-default bg-bg-app px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">
                            {cmd.shortcut}
                          </kbd>
                        )}
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              ))}
            </Command.List>
          </Command>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>
  );
};

export default CommandPalette;
