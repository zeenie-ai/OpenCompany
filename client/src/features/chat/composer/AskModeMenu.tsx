/**
 * Ask first by mode (RFC-0007 §4.2; wording R5 §5), beside the message box
 * in place of the Ask first chip once the employee's generation asks by
 * mode: a chip that opens "Ask me first", the three modes with what each
 * means, and "Allowed without asking…", what the employee does unasked
 * (AllowedWithoutAsking). A choice changes the workflow's live rule
 * (`set_ask_first` with `mode`), with no restart; one that asks less than
 * now asks first.
 */

import { ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';
import type { AskMode } from '../data/approvals';
import { AllowedWithoutAsking } from './AllowedWithoutAsking';

const MODES: readonly { id: AskMode; label: string; detail: (name: string) => string; title: (name: string) => string }[] = [
  {
    id: 'outside',
    label: 'Before anything outside',
    detail: (name) => `${name} asks before it messages, posts, books, deploys or changes things in your apps.`,
    title: (name) => `${name} asks before anything outside`,
  },
  {
    id: 'everything',
    label: 'Before everything',
    detail: () => 'Also before it changes its own files and notes.',
    title: (name) => `${name} asks before everything`,
  },
  {
    id: 'money_only',
    label: 'Only for money and deletes',
    detail: (name) => `${name} works on its own but asks before it spends money or deletes anything.`,
    title: (name) => `${name} asks only before money and deletes`,
  },
];

/** How much each mode asks: choosing one that asks less asks first. */
const RANK: Record<AskMode, number> = { money_only: 0, outside: 1, everything: 2 };

/** What a mode that asks less gives up. */
const ASKS_LESS: Record<Exclude<AskMode, 'everything'>, { title: (name: string) => string; detail: (name: string) => string; confirm: string }> = {
  outside: {
    title: (name) => `Let ${name} change its own files without asking?`,
    detail: (name) => `${name} will change its own files and notes straight away. It still asks before anything outside.`,
    confirm: 'Ask before anything outside',
  },
  money_only: {
    title: (name) => `Let ${name} work without asking?`,
    detail: (name) =>
      `Messages, posts, bookings and changes in your apps will go out straight away, without a card for you to check first. ${name} still asks before it spends money or deletes anything.`,
    confirm: 'Ask only for money and deletes',
  },
};

export function AskModeMenu({
  workflowId,
  mode,
  name,
  onChange,
  onError,
  disabled = false,
  compact = false,
}: {
  workflowId: string;
  /** null: no rule yet, so nothing asks. */
  mode: AskMode | null;
  name: string;
  onChange: (next: AskMode) => void;
  /** The list's errors. */
  onError: (message: string) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  // The mode that asks less stays set while its confirmation closes, so the
  // dialog keeps its words to the end.
  const [less, setLess] = useState<Exclude<AskMode, 'everything'> | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  const current = MODES.find((item) => item.id === mode);

  const choose = (value: string) => {
    const next = MODES.find((item) => item.id === value)?.id;
    if (!next || next === mode) return;
    if (mode && next !== 'everything' && RANK[next] < RANK[mode]) {
      setLess(next);
      setConfirming(true);
    } else onChange(next);
  };

  const copy = less ? ASKS_LESS[less] : null;
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild disabled={disabled}>
          <Button
            variant="chip"
            size="sm"
            aria-label="Ask first"
            title={current ? current.title(name) : `${name} doesn’t ask first`}
            className={cn(
              'h-6.5 gap-1.25 rounded-pill px-2.5 text-xs font-semibold text-fg-muted',
              current && 'border-action-config-border bg-action-config-soft text-action-config-ink',
              compact && 'h-6 px-2',
            )}
          >
            <ShieldCheck aria-hidden className="size-3.25" strokeWidth={2} />
            Ask first
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" sideOffset={8} className="w-(--w-chat-model-menu) max-w-[calc(100vw-2rem)]">
          <DropdownMenuLabel>Ask me first</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={mode ?? ''} onValueChange={choose}>
            {MODES.map((item) => (
              <DropdownMenuRadioItem key={item.id} value={item.id} className="py-1.5">
                <span className="flex min-w-0 flex-col gap-px">
                  <span className="font-medium">{item.label}</span>
                  <span className="text-xs text-muted-foreground">{item.detail(name)}</span>
                </span>
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setListOpen(true)}>Allowed without asking…</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{copy?.title(name)}</AlertDialogTitle>
            <AlertDialogDescription>{copy?.detail(name)}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep asking</AlertDialogCancel>
            <AlertDialogAction onClick={() => less && onChange(less)}>{copy?.confirm}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <AllowedWithoutAsking workflowId={workflowId} name={name} open={listOpen} onOpenChange={setListOpen} onError={onError} />
    </>
  );
}

export default AskModeMenu;
