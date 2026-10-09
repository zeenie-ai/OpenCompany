/**
 * The model picker (design handoff chat v2, "Model & thinking selector";
 * docs-internal/chat_protocol.md, "Model and thinking"): a button in the
 * message box's toolbar naming the model the employee answers on, and the
 * thinking level unless it is Balanced. It opens a panel above the box,
 * level with its right edge: Auto, then the offered models (a row the owner
 * can't pick says why), and under them, pinned, a slider for how hard the
 * model thinks, or a line saying the model sets its own pace.
 *
 * Two parts, because the panel is placed against the box, not the button:
 * `ModelPicker` wraps the message box, which becomes the panel's Radix
 * anchor, and holds the panel; `ModelPickerButton`, in the box's toolbar,
 * opens it. Radix places a popover against an anchor that contains its
 * button; an anchor anywhere else loses to the button, which registers as
 * the anchor too.
 *
 * ArrowUp and ArrowDown move through the models, Enter picks, ArrowLeft and
 * ArrowRight change the level, Esc closes; closing puts the cursor back in
 * the message box, and a pick settles the button with a small bounce. Every
 * word comes from the server (data/models.ts).
 */

import { Check, ChevronDown, Zap } from 'lucide-react';
import { createContext, useContext, useRef, useState, type KeyboardEvent, type ReactElement, type RefObject } from 'react';
import { Button } from '@/components/ui/button';
import { Command, CommandItem, CommandList, CommandSeparator } from '@/components/ui/command';
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { LevelSlider } from '@/components/ui/slider';
import { animate } from '@/lib/motion';
import type { ChatChoice, ChatLevel, ChatModel, ChatModels, Effort } from '../data/models';

/** What the button shows, from the picker around it. */
interface ButtonView {
  current: ChatModel;
  /** The level, unless it is Balanced or the model takes none. */
  level: ChatLevel | null;
  triggerRef: RefObject<HTMLButtonElement | null>;
}

const ButtonViewContext = createContext<ButtonView | null>(null);

function describe(models: ChatModels, choice: ChatChoice) {
  const rows = [models.auto, ...models.models];
  const current = rows.find((row) => row.id === choice.model) ?? models.auto;
  const levelIndex = models.levels.findIndex((level) => level.id === choice.effort);
  const level: ChatLevel | undefined = models.levels[levelIndex];
  return { rows, current, levelIndex, level };
}

export function ModelPicker({
  name,
  models,
  choice,
  onModel,
  onEffort,
  onOpen,
  boxRef,
  children,
}: {
  /** Who answers: the panel's and the slider's names say so. */
  name: string;
  /** Absent until they load, and when the employee takes no choice: then
   *  there is no button and no panel. */
  models: ChatModels | undefined;
  choice: ChatChoice | null;
  onModel: (id: string) => void;
  onEffort: (effort: Effort) => void;
  /** The panel opened (read the picker again). */
  onOpen?: () => void;
  /** Where the cursor goes back to. */
  boxRef: RefObject<HTMLTextAreaElement | null>;
  /** The message box, with the button inside it. */
  children: ReactElement;
}) {
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState('');
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const shown = models && choice ? describe(models, choice) : null;

  const toggle = (next: boolean) => {
    if (next && shown) {
      const { rows, current } = shown;
      setHighlight(current.available ? current.id : (rows.find((row) => row.available)?.id ?? current.id));
      onOpen?.();
    }
    setOpen(next);
  };

  const pick = (id: string) => {
    if (id !== choice?.model) onModel(id);
    setOpen(false);
    requestAnimationFrame(() =>
      animate(triggerRef.current, [{ transform: 'scale(0.96)' }, { transform: 'none' }], { duration: 'pick', easing: 'spring' }),
    );
  };

  const step = (by: number) => {
    const next = models?.levels[(shown?.levelIndex ?? -1) + by];
    if (shown?.current.effort && next) onEffort(next.id);
  };

  // ArrowLeft and ArrowRight change the level from the list too; the
  // slider has its own keys while it has the focus.
  const onListKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    step(event.key === 'ArrowRight' ? 1 : -1);
  };

  const button: ButtonView | null = shown && {
    current: shown.current,
    level: shown.current.effort && shown.level && shown.level.id !== '' ? shown.level : null,
    triggerRef,
  };

  return (
    <Popover open={open && shown !== null} onOpenChange={toggle}>
      <ButtonViewContext.Provider value={button}>
        <PopoverAnchor asChild>{children}</PopoverAnchor>
      </ButtonViewContext.Provider>
      {models && shown && (
        <PopoverContent
          side="top"
          align="end"
          sideOffset={8}
          aria-label={`Choose how ${name} answers`}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            listRef.current?.focus();
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            boxRef.current?.focus();
          }}
          className="flex max-h-[min(var(--h-chat-model-menu),calc(100vh-160px))] w-(--w-chat-model-menu) max-w-(--radix-popover-trigger-width) flex-col gap-1 rounded-xl border border-border-default bg-bg-elevated p-1.5 shadow-card-hover ring-0 duration-(--dur-popover-in) ease-spring"
        >
          <Command
            value={highlight}
            onValueChange={setHighlight}
            shouldFilter={false}
            loop
            label="Model"
            onKeyDown={onListKeyDown}
            className="min-h-0 flex-1 bg-transparent"
          >
            <CommandList ref={listRef} label="Model" className="max-h-none flex-1 outline-none [scrollbar-gutter:stable]">
              <ModelRow model={models.auto} chosen={shown.current.id === models.auto.id} onPick={pick} />
              <CommandSeparator className="mx-1.5 my-1" />
              {models.models.map((model) => (
                <ModelRow key={model.id} model={model} chosen={shown.current.id === model.id} onPick={pick} />
              ))}
            </CommandList>
          </Command>
          <div className="flex flex-none flex-col gap-2.5 border-t border-border-default px-2 pt-3 pb-2">
            <div className="flex items-center gap-2">
              <Zap aria-hidden strokeWidth={1.9} className="size-3.75 shrink-0 text-fg-faint" />
              <span className="min-w-0 flex-1 truncate text-center text-base font-semibold text-fg-default">
                {shown.current.short}
                {shown.current.effort && shown.level && <span className="font-medium text-fg-muted"> {shown.level.label}</span>}
              </span>
              <span aria-hidden className="w-3.75 shrink-0" />
            </div>
            {shown.current.effort && shown.level ? (
              <LevelSlider
                value={shown.levelIndex}
                count={models.levels.length}
                onValueChange={(index) => onEffort(models.levels[index].id)}
                label={`How hard ${name} thinks`}
                valueText={shown.level.label}
                title={shown.level.hint}
              />
            ) : (
              <p className="m-0 text-center text-xs text-fg-muted">{shown.current.effortNote}</p>
            )}
          </div>
        </PopoverContent>
      )}
    </Popover>
  );
}

/** The picker's button, in the message box's toolbar inside `ModelPicker`;
 *  nothing until the picker has its rows. */
export function ModelPickerButton() {
  const view = useContext(ButtonViewContext);
  if (!view) return null;
  return (
    <PopoverTrigger asChild>
      <Button
        ref={view.triggerRef}
        variant="quiet"
        aria-haspopup="dialog"
        title="Model and effort"
        className="group/picker h-8.5 min-w-0 gap-1.5 rounded-pill pr-2 pl-3 text-sm font-medium aria-expanded:bg-bg-active"
      >
        <span className="truncate text-fg-default">{view.current.short}</span>
        {view.level && <span className="shrink-0">· {view.level.label}</span>}
        <ChevronDown
          aria-hidden
          strokeWidth={2}
          className="size-3.25 shrink-0 transition-transform duration-(--dur-default) group-aria-expanded/picker:rotate-180 motion-reduce:transition-none"
        />
      </Button>
    </PopoverTrigger>
  );
}

function ModelRow({ model, chosen, onPick }: { model: ChatModel; chosen: boolean; onPick: (id: string) => void }) {
  return (
    <CommandItem
      value={model.id}
      disabled={!model.available}
      onSelect={() => onPick(model.id)}
      className="min-h-11 gap-2.5 rounded-lg px-2.5 py-1.75 text-fg-default transition-colors duration-(--dur-fast)"
    >
      <span className="flex min-w-0 flex-1 flex-col gap-px">
        <span className="text-sm font-medium">{model.name}</span>
        <span className="text-xs text-fg-muted">{model.available ? model.description : model.reason}</span>
      </span>
      {chosen && <Check aria-hidden strokeWidth={2.2} className="size-4 text-fg-default" />}
    </CommandItem>
  );
}
