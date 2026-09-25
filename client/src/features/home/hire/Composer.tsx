/**
 * The hire composer (design handoff "Composer"): the job description box,
 * the connected-apps pill, and Create.
 *
 * Keys: Enter sends, Shift+Enter starts a new line, Escape stops editing
 * the draft. While an input method is composing (Japanese, Chinese, Korean
 * and other IMEs), Enter confirms the composition and never sends.
 *
 * Presentational: the hire view owns what submitting does (the AI check,
 * the draft request).
 */

import { ArrowRight } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, type KeyboardEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { animate } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { AppMark } from '../ui/primitives';
import { createLabel, isSendKey } from './composerKeys';

export interface ComposerApp {
  id: string;
  name: string;
  icon_ref?: string | null;
}

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** Editing the current draft instead of describing a new job. */
  refining: boolean;
  onStopRefining: () => void;
  /** A setup is being written; Create waits for it. */
  working: boolean;
  apps: ComposerApp[];
  onOpenApps: () => void;
  /** Change this number to move focus into the box. */
  focusNonce?: number;
  maxLength?: number;
}

const APP_STACK = 3;

export function Composer({
  value,
  onChange,
  onSubmit,
  refining,
  onStopRefining,
  working,
  apps,
  onOpenApps,
  focusNonce = 0,
  maxLength,
}: ComposerProps) {
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const createRef = useRef<HTMLButtonElement>(null);
  const canCreate = value.trim().length > 0 && !working;

  // Grow with the text up to --h-composer-max, then scroll. Runs on every
  // value change so text set from outside (a template) sizes the box too.
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    box.style.height = 'auto';
    box.style.height = `${box.scrollHeight}px`;
    box.style.overflowY = box.scrollHeight > box.clientHeight ? 'auto' : 'hidden';
  }, [value]);

  useEffect(() => {
    if (!focusNonce) return;
    const box = boxRef.current;
    box?.focus();
    if (box) box.setSelectionRange(box.value.length, box.value.length);
  }, [focusNonce]);

  // Starting to edit the draft pulls focus here with a soft ring.
  useEffect(() => {
    if (!refining) return;
    boxRef.current?.focus();
    animate(
      surfaceRef.current,
      [{ boxShadow: '0 0 0 0 var(--node-agent-edge)' }, { boxShadow: '0 0 0 6px transparent' }],
      { duration: 900, fill: 'none' },
    );
  }, [refining]);

  const submit = () => {
    if (!canCreate) return;
    animate(createRef.current, [{ transform: 'scale(1)' }, { transform: 'scale(.94)', offset: 0.3 }, { transform: 'scale(1)' }], {
      duration: 360,
      fill: 'none',
    });
    boxRef.current?.blur();
    onSubmit();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (isSendKey(event)) {
      event.preventDefault();
      submit();
    } else if (event.key === 'Escape' && refining) {
      event.preventDefault();
      onStopRefining();
    }
  };

  const stack = apps.slice(0, APP_STACK);
  return (
    <div
      ref={surfaceRef}
      data-intro
      className={cn(
        'relative flex w-full max-w-(--w-composer) shrink-0 flex-col gap-2 rounded-composer border bg-bg-panel pt-3 pr-3 pb-2.5 pl-4 shadow-float transition-colors duration-(--dur-slow)',
        refining ? 'border-node-agent-edge' : 'border-border-default focus-within:border-border-strong',
      )}
    >
      <Textarea
        ref={boxRef}
        variant="bare"
        rows={2}
        value={value}
        maxLength={maxLength}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        aria-label={refining ? 'What should change?' : 'Describe the job'}
        placeholder={
          refining
            ? 'What should change? e.g. Only reply during business hours'
            : 'e.g. Answer customer messages on WhatsApp and book appointments'
        }
        className="max-h-(--h-composer-max) overflow-hidden py-1.5 text-lead leading-normal text-fg-default"
      />
      <div className="flex items-center gap-2">
        <Button
          variant="quiet"
          onClick={onOpenApps}
          title="Apps your employees can use"
          className="h-8.5 gap-2 rounded-pill border-border-default pr-3 pl-1.5 text-sm"
        >
          {stack.length > 0 && (
            <span className="flex" aria-hidden>
              {stack.map((app) => (
                <AppMark
                  key={app.id}
                  name={app.name}
                  iconRef={app.icon_ref}
                  size="sm"
                  className="-mr-1.5 ring-2 ring-bg-panel"
                />
              ))}
            </span>
          )}
          <span className={cn(stack.length > 0 && 'ml-1.5')}>
            {apps.length} {apps.length === 1 ? 'app' : 'apps'}
          </span>
        </Button>
        {refining && <span className="text-xs font-medium text-node-agent-ink">Editing the draft</span>}
        <Button
          ref={createRef}
          variant="invert"
          size="pill"
          disabled={!canCreate}
          onClick={submit}
          className="ml-auto"
        >
          {createLabel(working, refining)}
          <ArrowRight aria-hidden strokeWidth={2.25} />
        </Button>
      </div>
    </div>
  );
}

export default Composer;
