/**
 * The model picker (design handoff chat v2, "Model & thinking selector";
 * docs-internal/chat_protocol.md, "Model and thinking"): a button in a
 * message box's toolbar naming the model, and the thinking level unless it
 * is Balanced, and a panel (`PickerBody`): the model menu the editor's Set
 * Global Model shows too (components/ui/ModelMenu.tsx), here with Auto first
 * and then each connected provider's models under its name (a row the owner
 * can't pick says why), and with it a slider for how hard the model thinks,
 * or a line saying the model sets its own pace.
 *
 * Two hosts, one panel:
 * - The chat's message box, at the foot of the page: the panel floats above
 *   the box, level with its right edge. `ModelPicker` wraps the box, which
 *   becomes the panel's Radix anchor, and holds the panel;
 *   `ModelPickerButton`, in the box's toolbar, opens it. Radix places a
 *   popover against an anchor that contains its button; an anchor anywhere
 *   else loses to the button, which registers as the anchor too.
 * - Home's hire composer, under the hero: the panel opens in the page under
 *   the box (`ModelPickerSection`), pushing what follows down, so it never
 *   covers the heading or runs off the screen; on a wide screen the level
 *   sits beside the list. `ModelPickerToggle`, in the box's toolbar, opens
 *   and closes it.
 *
 * ArrowUp and ArrowDown move through the models, Enter picks, ArrowLeft and
 * ArrowRight change the level, Esc closes; a pick or Esc puts the cursor
 * back in the box, and a pick settles the button with a small bounce. Every
 * row's words come from the server (data/models.ts).
 */

import { ChevronDown, Zap } from 'lucide-react';
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ComponentProps,
  type KeyboardEvent,
  type ReactElement,
  type RefObject,
} from 'react';
import { Button } from '@/components/ui/button';
import { ModelMenu, ModelMenuContent, type ModelMenuGroup, type ModelMenuItem } from '@/components/ui/ModelMenu';
import { Popover, PopoverAnchor, PopoverTrigger } from '@/components/ui/popover';
import { LevelSlider } from '@/components/ui/slider';
import { animate } from '@/lib/motion';
import { prefersReducedMotion } from '@/lib/useReducedMotion';
import { cn } from '@/lib/utils';
import type { ChatChoice, ChatLevel, ChatModel, ChatModels, Effort } from '../data/models';

/** The chosen row and its level. */
interface Shown {
  current: ChatModel;
  levelIndex: number;
  level: ChatLevel | undefined;
}

function describe(models: ChatModels, choice: ChatChoice): Shown {
  const rows = [models.auto, ...models.models];
  const current = rows.find((row) => row.id === choice.model) ?? models.auto;
  const levelIndex = models.levels.findIndex((level) => level.id === choice.effort);
  return { current, levelIndex, level: models.levels[levelIndex] };
}

/** The level the button names: none at Balanced, or for a model that takes none. */
function namedLevel(shown: Shown): ChatLevel | null {
  return shown.current.effort && shown.level && shown.level.id !== '' ? shown.level : null;
}

/** A model's row: its name, and Auto's line or why it can't be picked. */
const itemOf = (model: ChatModel): ModelMenuItem => ({
  value: model.id,
  label: model.name,
  line: model.available ? model.description : model.reason,
  disabled: !model.available,
});

/** The models under their providers' names, in the server's order. */
function byProvider(models: readonly ChatModel[]): ModelMenuGroup[] {
  const groups = new Map<string, { key: string; heading: string; items: ModelMenuItem[] }>();
  for (const model of models) {
    const key = model.provider ?? '';
    const group = groups.get(key) ?? { key, heading: model.providerName ?? key, items: [] };
    group.items.push(itemOf(model));
    groups.set(key, group);
  }
  return [...groups.values()];
}

/** The button settles after a pick. */
function bounce(button: HTMLButtonElement | null) {
  requestAnimationFrame(() =>
    animate(button, [{ transform: 'scale(0.96)' }, { transform: 'none' }], { duration: 'pick', easing: 'spring' }),
  );
}

export interface ModelPickerProps {
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
}

/** The panel: the model menu, and the level. `levelClassName` lays the level
 *  out for its host. */
function PickerBody({
  name,
  models,
  shown,
  onPick,
  onEffort,
  listRef,
  levelClassName,
}: {
  name: string;
  models: ChatModels;
  shown: Shown;
  onPick: (id: string) => void;
  onEffort: (effort: Effort) => void;
  listRef: RefObject<HTMLDivElement | null>;
  levelClassName?: string;
}) {
  // ArrowLeft and ArrowRight change the level from the list too; the
  // slider has its own keys while it has the focus.
  const onListKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const next = models.levels[shown.levelIndex + (event.key === 'ArrowRight' ? 1 : -1)];
    if (shown.current.effort && next) onEffort(next.id);
  };

  return (
    <>
      <ModelMenu
        label="Model"
        lead={[itemOf(models.auto)]}
        groups={byProvider(models.models)}
        value={shown.current.id}
        onSelect={onPick}
        onKeyDown={onListKeyDown}
        listRef={listRef}
      />
      <div className={cn('flex flex-none flex-col gap-2.5 border-t border-border-default px-2 pt-3 pb-2', levelClassName)}>
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
    </>
  );
}

/** The button: the model's short name, the level unless it is Balanced, and
 *  a chevron that turns while the panel is open. */
function PickerFace({ shown, ...props }: ComponentProps<typeof Button> & { shown: Shown }) {
  const level = namedLevel(shown);
  return (
    <Button
      variant="quiet"
      title="Model and effort"
      className="group/picker h-8.5 min-w-0 gap-1.5 rounded-pill pr-2 pl-3 text-sm font-medium aria-expanded:bg-bg-active"
      {...props}
    >
      <span className="truncate text-fg-default">{shown.current.short}</span>
      {level && <span className="shrink-0">· {level.label}</span>}
      <ChevronDown
        aria-hidden
        strokeWidth={2}
        className="size-3.25 shrink-0 transition-transform duration-(--dur-default) group-aria-expanded/picker:rotate-180 motion-reduce:transition-none"
      />
    </Button>
  );
}

// ----- the chat: a panel floating above the box -----

/** What the button shows, from the picker around it. */
interface ButtonView {
  shown: Shown;
  triggerRef: RefObject<HTMLButtonElement | null>;
}

const ButtonViewContext = createContext<ButtonView | null>(null);

export function ModelPicker({ name, models, choice, onModel, onEffort, onOpen, boxRef, children }: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const shown = models && choice ? describe(models, choice) : null;

  const toggle = (next: boolean) => {
    if (next && shown) onOpen?.();
    setOpen(next);
  };

  const pick = (id: string) => {
    if (id !== choice?.model) onModel(id);
    setOpen(false);
    bounce(triggerRef.current);
  };

  return (
    <Popover open={open && shown !== null} onOpenChange={toggle}>
      <ButtonViewContext.Provider value={shown && { shown, triggerRef }}>
        <PopoverAnchor asChild>{children}</PopoverAnchor>
      </ButtonViewContext.Provider>
      {models && shown && (
        <ModelMenuContent
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
          className="max-w-(--radix-popover-trigger-width)"
        >
          <PickerBody name={name} models={models} shown={shown} onPick={pick} onEffort={onEffort} listRef={listRef} />
        </ModelMenuContent>
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
      <PickerFace ref={view.triggerRef} shown={view.shown} aria-haspopup="dialog" />
    </PopoverTrigger>
  );
}

// ----- the hire composer: a section in the page under the box -----

/** What the toggle shows and does, from the section around it. */
interface ToggleView {
  shown: Shown;
  open: boolean;
  sectionId: string;
  triggerRef: RefObject<HTMLButtonElement | null>;
  toggle: () => void;
}

const ToggleViewContext = createContext<ToggleView | null>(null);

export function ModelPickerSection({ name, models, choice, onModel, onEffort, onOpen, boxRef, children }: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const sectionId = useId();
  const shown = models && choice ? describe(models, choice) : null;
  const visible = open && shown !== null;

  // Opening hands the list the focus and brings the section into view.
  useEffect(() => {
    if (!visible) return;
    listRef.current?.focus({ preventScroll: true });
    sectionRef.current?.scrollIntoView?.({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'nearest' });
  }, [visible]);

  const toggle = () => {
    if (!open && shown) onOpen?.();
    setOpen(!open);
  };

  /** Close, and put the cursor back in the box. */
  const close = () => {
    setOpen(false);
    boxRef.current?.focus();
  };

  const pick = (id: string) => {
    if (id !== choice?.model) onModel(id);
    close();
    bounce(triggerRef.current);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    close();
  };

  return (
    <ToggleViewContext.Provider value={shown && { shown, open: visible, sectionId, triggerRef, toggle }}>
      {children}
      {models && shown && visible && (
        <section
          ref={sectionRef}
          id={sectionId}
          aria-label={`Choose how ${name} answers`}
          onKeyDown={onKeyDown}
          className="mt-3 flex max-h-(--h-hire-model-section) w-full max-w-(--w-composer) shrink-0 scroll-mt-4 scroll-mb-4 flex-col gap-1 rounded-xl border border-border-default bg-bg-elevated p-1.5 duration-(--dur-popover-in) animate-in fade-in-0 slide-in-from-top-2 motion-reduce:animate-none sm:flex-row"
        >
          <PickerBody
            name={name}
            models={models}
            shown={shown}
            onPick={pick}
            onEffort={onEffort}
            listRef={listRef}
            levelClassName="sm:w-(--w-chat-model-menu) sm:justify-center sm:border-t-0 sm:border-l sm:px-4"
          />
        </section>
      )}
    </ToggleViewContext.Provider>
  );
}

/** The section's toggle, in the box's toolbar inside `ModelPickerSection`;
 *  nothing until the picker has its rows. */
export function ModelPickerToggle() {
  const view = useContext(ToggleViewContext);
  if (!view) return null;
  return (
    <PickerFace
      ref={view.triggerRef}
      shown={view.shown}
      aria-expanded={view.open}
      aria-controls={view.open ? view.sectionId : undefined}
      onClick={view.toggle}
    />
  );
}
