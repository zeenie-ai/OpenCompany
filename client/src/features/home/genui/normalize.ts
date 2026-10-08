/**
 * Turning a model's setup-screen spec into one that is safe to render.
 *
 * A spec is flat, in json-render's shape: `{root, state, elements: {id:
 * {type, props, children, visible?, on?}}}`. Models get it slightly wrong
 * in predictable ways (props written beside `props`, children never
 * listed, the hire button left out, a cycle), so this repairs rather than
 * rejects. The result:
 *
 * - is a tree: the root is always a vertical Stack; each element has one
 *   parent (the first one found, depth first), cycles are broken, and only
 *   Stack / Grid / Card keep children;
 * - loses nothing a model placed badly: elements nobody lists are attached
 *   where they belong (controls into the rules card, connect buttons into
 *   the apps card, anything else under the root);
 * - gives every Button its action as `on.press` ({action, params}), whether
 *   the model wrote that or the older `action` / `actionParams` props, and
 *   every Toggle its value as `checked` (older replies say `value`);
 * - is laid out as the setup card (onboarding handoff R2), the root's
 *   children in this order, named in `layout`: the agent card (the first
 *   one: who they are), the routine, everything else in the order written,
 *   then the footer strip's two: "Ask me before sending anything" bound to
 *   /rules/askFirst (on by default) and a row of one change button and one
 *   hire button;
 * - always says when they work: the routine is the first Plan, its When row
 *   bound to /trigger, else one Schedule bound to it. /trigger starts as
 *   the hire button's trigger (else the app the routine's first "When" step
 *   names, else the owner messaging them), snapped to one the server builds
 *   exactly as it reads;
 * - has at most LIMITS.maxElements elements (the card's own pieces are
 *   always kept);
 * - cannot reach an object prototype: ids, prop keys and state keys named
 *   `__proto__`, `constructor` or `prototype` are dropped, and so is any
 *   expression whose path goes through one; `watch`, `repeat`, `slots`,
 *   `$computed` and an action's `confirm` never get through
 *   (lib/jsonRender/sanitize.ts).
 *
 * `ask` buttons become plain `refine`: the owner writes the change, the
 * model's suggested wording is never sent on their behalf.
 *
 * Returns null when nothing renderable came back. The server applies the
 * same "anything renderable" rule before retrying a reply
 * (services/employees/setup_reply.py).
 */

import type { VisibilityCondition } from '@json-render/core';
// The file, not the index: this module is in Home's first chunk.
import {
  DEFAULT_SANITIZE_LIMITS,
  sanitizeCondition,
  sanitizeExpressions,
  type SanitizeLimits,
} from '@/lib/jsonRender/sanitize';
import {
  ASK_FIRST_LABEL,
  LIMITS,
  PROP_SCHEMAS,
  STATE_PATHS,
  actionOf,
  isComponentType,
  isContainer,
  isControl,
  type ActionType,
  type ComponentType,
} from './catalog';
import { bindingPath, getPath, isForbiddenKey, resolveValue, sanitizeState, setPath, type UiState } from './expressions';
import { readTrigger, snapTrigger, type HireTrigger } from './hirePayload';

/** What pressing a Button runs: an action and its params (expressions
 *  resolved against the screen's state at the press). */
export interface PressBinding {
  action: ActionType;
  params?: Record<string, unknown>;
}

export interface SpecElement {
  type: ComponentType;
  /** Props as written (expressions unresolved), made safe to resolve. */
  props: Record<string, unknown>;
  children: string[];
  visible?: VisibilityCondition;
  /** A Button's action. */
  on?: { press: PressBinding };
}

/** Where the card draws the root's children (HireScreen). */
export interface CardLayout {
  /** The agent card, the identity row; null when the model wrote none. */
  identity: string | null;
  /** The routine, then everything else, in order. */
  body: string[];
  /** The footer strip: the ask-first toggle on the left... */
  askFirst: string;
  /** ...and on the right a row of the change button, then the hire button. */
  actions: string;
}

export interface NormalizedSpec {
  root: string;
  state: UiState;
  elements: Record<string, SpecElement>;
  /** Element ids in render order (depth first), for the reveal. */
  order: string[];
  layout: CardLayout;
}

/** Element keys that are never props (everything else beside `props` is
 *  hoisted into them). The server reads replies with the same list
 *  (services/employees/setup_reply.py `_META_KEYS`). */
const META_KEYS = new Set(['type', 'props', 'children', 'visible', 'watch', 'id', 'on', 'repeat', 'slots']);
const ID_PATTERN = /^[A-Za-z0-9_.:-]+$/;
const MAX_RAW_ELEMENTS = 64;
const RULES_TITLE = /rule|setting|prefer|control|how/i;
const APPS_TITLE = /app|connect|need|check/i;
const ASK_FIRST_WORDING = /\bask\b.*\b(before|first)\b/i;

/** How much of a prop the normaliser keeps (a Draft's body is the longest). */
const PROP_LIMITS: SanitizeLimits = { ...DEFAULT_SANITIZE_LIMITS, maxIdLength: LIMITS.maxIdLength, maxString: LIMITS.maxBody };
/** A button's params: shorter strings, shallower values. */
const PARAM_LIMITS: SanitizeLimits = { ...PROP_LIMITS, maxString: LIMITS.maxText, maxDepth: 4, maxArray: 20 };

type Elements = Map<string, SpecElement>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function usableId(id: string): boolean {
  return id.length <= LIMITS.maxIdLength && ID_PATTERN.test(id) && !isForbiddenKey(id);
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value).trim() : '';
}

function pressBinding(action: ActionType, rawParams: unknown): PressBinding {
  if (action === 'refine') return { action };
  const params = sanitizeExpressions(rawParams ?? {}, PARAM_LIMITS, 1);
  return isRecord(params) && Object.keys(params).length > 0 ? { action, params } : { action };
}

/** The first binding under `on.press` (one, or a list) whose action is known. */
function pressOf(on: unknown): PressBinding | null {
  if (!isRecord(on)) return null;
  const bindings = Array.isArray(on.press) ? on.press : [on.press];
  for (const binding of bindings) {
    if (!isRecord(binding)) continue;
    const action = actionOf(binding.action);
    if (action) return pressBinding(action, binding.params);
  }
  return null;
}

/** A Button's action: `on.press` beside its props (json-render's place), or
 *  inside them, else the older `action` / `actionParams` props. */
function buttonPress(raw: Record<string, unknown>, written: Record<string, unknown>): PressBinding | null {
  const pressed = pressOf(raw.on) ?? pressOf(written.on);
  if (pressed) return pressed;
  const action = actionOf(written.action);
  return action ? pressBinding(action, written.actionParams) : null;
}

/** One element as the renderer will get it; null when it cannot be used: a
 *  Button needs a label and a known action, an agent card a name. */
function shape(type: ComponentType, written: Record<string, unknown>, raw: Record<string, unknown>, children: string[]): SpecElement | null {
  const press = type === 'Button' ? buttonPress(raw, written) : null;
  if (type === 'Button' && (!text(written.label) || !press)) return null;
  if (type === 'AgentCard' && !text(written.name)) return null;
  delete written.on;
  if (type === 'Button') {
    delete written.action;
    delete written.actionParams;
  }
  if (type === 'Toggle') {
    if (!('checked' in written) && 'value' in written) written.checked = written.value;
    delete written.value;
  }
  const props = sanitizeExpressions(written, PROP_LIMITS);
  const element: SpecElement = { type, props: isRecord(props) ? props : {}, children };
  const visible = sanitizeCondition(raw.visible, PROP_LIMITS);
  if (visible !== undefined) element.visible = visible;
  if (press) element.on = { press };
  return element;
}

function collect(raw: Record<string, unknown>): Elements {
  const out: Elements = new Map();
  for (const [id, value] of Object.entries(raw)) {
    if (out.size >= MAX_RAW_ELEMENTS) break;
    if (!usableId(id) || !isRecord(value) || !isComponentType(value.type)) continue;
    const written: Record<string, unknown> = {};
    for (const key of Object.keys(value)) {
      if (!META_KEYS.has(key) && !isForbiddenKey(key)) written[key] = value[key];
    }
    if (isRecord(value.props)) {
      for (const key of Object.keys(value.props)) {
        if (!isForbiddenKey(key)) written[key] = value.props[key];
      }
    }
    const children = Array.isArray(value.children)
      ? value.children.filter((child): child is string => typeof child === 'string')
      : [];
    const element = shape(value.type, written, value, children);
    if (element) out.set(id, element);
  }
  return out;
}

function freshId(elements: Elements, base: string): string {
  if (!elements.has(base)) return base;
  for (let n = 2; ; n++) if (!elements.has(`${base}${n}`)) return `${base}${n}`;
}

function isVerticalStack(element: SpecElement): boolean {
  return element.type === 'Stack' && element.props.direction !== 'horizontal';
}

function buttonAction(element: SpecElement | undefined): ActionType | null {
  return element?.type === 'Button' ? (element.on?.press.action ?? null) : null;
}

/** Attach `id`'s subtree: keep only children that exist and are not yet
 *  placed, depth first. Leaves lose any children they listed. */
function place(elements: Elements, id: string, placed: Set<string>) {
  placed.add(id);
  const element = elements.get(id)!;
  if (!isContainer(element.type)) {
    element.children = [];
    return;
  }
  const kept: string[] = [];
  for (const child of element.children) {
    if (!elements.has(child) || placed.has(child)) continue;
    kept.push(child);
    place(elements, child, placed);
  }
  element.children = kept;
}

function depthFirst(elements: Elements, root: string): string[] {
  const order: string[] = [];
  const walk = (id: string) => {
    const element = elements.get(id);
    if (!element) return;
    order.push(id);
    element.children.forEach(walk);
  };
  walk(root);
  return order;
}

/** Take `id` out of its parent's children; the element itself stays. */
function detach(elements: Elements, id: string): void {
  for (const element of elements.values()) {
    const index = element.children.indexOf(id);
    if (index !== -1) element.children.splice(index, 1);
  }
}

function removeEverywhere(elements: Elements, id: string): void {
  elements.delete(id);
  detach(elements, id);
}

/** When they work, as the screen first says it: the hire button's trigger,
 *  else the app the routine's first "When" step names, else the owner
 *  messaging them. */
function seedTrigger(hire: SpecElement, plan: SpecElement | undefined, state: UiState): HireTrigger {
  const params = resolveValue(hire.on?.press.params, state);
  const given = readTrigger(isRecord(params) ? params.trigger : undefined);
  if (given && (given.kind !== 'app_event' || given.app)) return given;
  const parsed = plan ? PROP_SCHEMAS.Plan.safeParse(resolveValue(plan.props, state) ?? {}) : null;
  const app = parsed?.success ? parsed.data.steps.find((step) => step.role === 'trigger')?.app : undefined;
  return snapTrigger(app ? { kind: 'app_event', app } : { kind: 'manual' });
}

function hireButton(agent: SpecElement | undefined): SpecElement {
  const name = text(agent?.props.name);
  const role = text(agent?.props.role);
  const apps = Array.isArray(agent?.props.apps) ? agent!.props.apps.filter((app) => typeof app === 'string') : [];
  const params = { ...(name ? { name } : {}), ...(role ? { role } : {}), apps };
  return {
    type: 'Button',
    props: { label: name ? `Hire ${name}` : 'Hire them', variant: 'primary' },
    on: { press: { action: 'hire_employee', params } },
    children: [],
  };
}

export function normalizeSpec(spec: unknown): NormalizedSpec | null {
  if (!isRecord(spec) || !isRecord(spec.elements)) return null;
  const elements = collect(spec.elements);
  if (elements.size === 0) return null;
  let state = (sanitizeState(isRecord(spec.state) ? spec.state : {}, LIMITS.maxInput) ?? {}) as UiState;

  // The root is always a vertical Stack.
  const declared = typeof spec.root === 'string' && elements.has(spec.root) ? spec.root : null;
  let root: string;
  if (declared && isVerticalStack(elements.get(declared)!)) {
    root = declared;
  } else {
    root = freshId(elements, '__root');
    elements.set(root, { type: 'Stack', props: { direction: 'vertical', gap: 'md' }, children: declared ? [declared] : [] });
  }

  const placed = new Set<string>();
  place(elements, root, placed);

  // Subtrees nobody listed: attach their tops where they belong. An orphan
  // listed by an orphan container travels with it; orphans that only list
  // each other (a cycle) go in as tops themselves.
  const orphans = [...elements.keys()].filter((id) => !placed.has(id));
  const claimed = new Set(
    orphans.filter((id) => isContainer(elements.get(id)!.type)).flatMap((id) => elements.get(id)!.children),
  );
  const tops = orphans.filter((id) => !claimed.has(id));
  const carried = new Set<string>();
  const carry = (id: string) => {
    if (carried.has(id)) return;
    carried.add(id);
    const element = elements.get(id)!;
    if (!isContainer(element.type)) return;
    for (const child of element.children) if (elements.has(child) && !placed.has(child)) carry(child);
  };
  tops.forEach(carry);
  const cyclic = orphans.filter((id) => !carried.has(id));
  const sorted = { controls: [] as string[], connects: [] as string[], others: [] as string[] };
  const sort = (id: string) => {
    const element = elements.get(id)!;
    if (isControl(element.type)) sorted.controls.push(id);
    else if (buttonAction(element) === 'connect_app') sorted.connects.push(id);
    else sorted.others.push(id);
  };
  const attach = (host: string, ids: string[]) => {
    for (const id of ids) {
      if (placed.has(id)) continue;
      elements.get(host)!.children.push(id);
      place(elements, id, placed);
    }
  };
  tops.forEach(sort);
  cyclic.forEach(sort);

  const cards = () => depthFirst(elements, root).filter((id) => elements.get(id)!.type === 'Card');
  const emptyCard = () => cards().find((id) => elements.get(id)!.children.length === 0);
  const titled = (ids: string[], pattern: RegExp) => ids.find((id) => pattern.test(text(elements.get(id)!.props.title)));
  // Unlisted cards go in first, so unlisted controls can find them.
  attach(root, sorted.others);
  attach(titled(cards(), RULES_TITLE) ?? emptyCard() ?? root, sorted.controls);
  attach(titled(cards(), APPS_TITLE) ?? emptyCard() ?? root, sorted.connects);

  for (const id of [...elements.keys()]) if (!placed.has(id)) elements.delete(id);

  // One agent card, one Plan, one hire button and one change button: the
  // first of each, depth first.
  const firstOf = (match: (element: SpecElement) => boolean): string | undefined => {
    const ids = depthFirst(elements, root).filter((id) => match(elements.get(id)!));
    ids.slice(1).forEach((id) => removeEverywhere(elements, id));
    return ids[0];
  };
  const agent = firstOf((element) => element.type === 'AgentCard');
  const plan = firstOf((element) => element.type === 'Plan');
  let hire = firstOf((element) => buttonAction(element) === 'hire_employee');
  let change = firstOf((element) => buttonAction(element) === 'refine');

  // "Ask me before sending anything" is always there, on by default.
  const order = depthFirst(elements, root);
  let askFirst = order.find((id) => {
    const element = elements.get(id)!;
    return element.type === 'Toggle' && bindingPath(element.props.checked) === STATE_PATHS.askFirst;
  });
  if (!askFirst) {
    const worded = order.find((id) => {
      const element = elements.get(id)!;
      return element.type === 'Toggle' && ASK_FIRST_WORDING.test(text(element.props.label));
    });
    if (worded) {
      const element = elements.get(worded)!;
      const previous = getPath(state, bindingPath(element.props.checked));
      element.props.checked = { $bindState: STATE_PATHS.askFirst };
      if (typeof previous === 'boolean') state = setPath(state, STATE_PATHS.askFirst, previous);
      askFirst = worded;
    }
  }
  if (typeof getPath(state, STATE_PATHS.askFirst) !== 'boolean') state = setPath(state, STATE_PATHS.askFirst, true);
  if (!askFirst) {
    askFirst = freshId(elements, '__askFirst');
    elements.set(askFirst, {
      type: 'Toggle',
      props: { label: ASK_FIRST_LABEL, checked: { $bindState: STATE_PATHS.askFirst } },
      children: [],
    });
  }

  // The hire button (primary) and the change button (secondary).
  if (!hire) {
    hire = freshId(elements, '__hire');
    elements.set(hire, hireButton(agent ? elements.get(agent) : undefined));
  }
  if (!change) {
    change = freshId(elements, '__edit');
    elements.set(change, { type: 'Button', props: { label: 'Change something' }, on: { press: { action: 'refine' } }, children: [] });
  }
  elements.get(hire)!.props.variant = 'primary';
  elements.get(change)!.props.variant = 'secondary';

  // When they work, bound to /trigger: the Plan's When row, else one
  // Schedule. Hire reads /trigger before the button.
  state = setPath(state, STATE_PATHS.trigger, seedTrigger(elements.get(hire)!, plan ? elements.get(plan) : undefined, state));
  const schedules = depthFirst(elements, root).filter((id) => elements.get(id)!.type === 'Schedule');
  let routine: string;
  if (plan) {
    schedules.forEach((id) => removeEverywhere(elements, id));
    elements.get(plan)!.props.trigger = { $bindState: STATE_PATHS.trigger };
    routine = plan;
  } else {
    schedules.slice(1).forEach((id) => removeEverywhere(elements, id));
    routine = schedules[0] ?? freshId(elements, '__schedule');
    elements.set(routine, { type: 'Schedule', props: { value: { $bindState: STATE_PATHS.trigger } }, children: [] });
  }

  // The card: who they are, the routine, everything else as written, then
  // the footer strip's toggle and buttons. All of these are leaves, so
  // moving one never takes anything with it.
  const pieces = [agent, routine, askFirst, change, hire].filter((id) => id !== undefined);
  pieces.forEach((id) => detach(elements, id));
  const actions = freshId(elements, '__actions');
  elements.set(actions, { type: 'Stack', props: { direction: 'horizontal', gap: 'sm' }, children: [change, hire] });
  const top = elements.get(root)!;
  top.children = [...(agent ? [agent] : []), routine, ...top.children, askFirst, actions];

  // Cap the size, always keeping the root and the card's own pieces.
  const full = depthFirst(elements, root);
  const parentOf = new Map<string, string>();
  for (const id of full) for (const child of elements.get(id)!.children) parentOf.set(child, id);
  const mustKeep = new Set<string>([root, actions, ...pieces]);
  let budget = LIMITS.maxElements - mustKeep.size;
  const kept = new Set<string>();
  for (const id of full) {
    const parent = parentOf.get(id);
    if (mustKeep.has(id) || (budget > 0 && (parent === undefined || kept.has(parent)))) {
      if (!mustKeep.has(id)) budget--;
      kept.add(id);
    }
  }
  for (const id of full) if (!kept.has(id)) removeEverywhere(elements, id);

  // Drop containers left empty (a Card with a subtitle still says something).
  for (let changed = true; changed; ) {
    changed = false;
    for (const [id, element] of elements) {
      if (id === root || !isContainer(element.type) || element.children.length > 0) continue;
      if (element.type === 'Card' && text(element.props.subtitle)) continue;
      removeEverywhere(elements, id);
      changed = true;
    }
  }

  // No prototype: ids are looked up here as keys.
  const record: Record<string, SpecElement> = Object.create(null);
  for (const [id, element] of elements) record[id] = element;
  const layout: CardLayout = {
    identity: agent ?? null,
    body: elements.get(root)!.children.filter((id) => id !== agent && id !== askFirst && id !== actions),
    askFirst,
    actions,
  };
  return { root, state, elements: record, order: depthFirst(elements, root), layout };
}
