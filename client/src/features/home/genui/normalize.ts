/**
 * Turning a model's setup-screen spec into one that is safe to render.
 *
 * A spec is flat: `{root, state, elements: {id: {type, props, children,
 * visible?}}}`. Models get it slightly wrong in predictable ways (props
 * written beside `props`, children never listed, the hire button left out,
 * a cycle), so this repairs rather than rejects. The result:
 *
 * - is a tree: the root is always a vertical Stack; each element has one
 *   parent (the first one found, depth first), cycles are broken, and only
 *   Stack / Grid / Card keep children;
 * - loses nothing a model placed badly: elements nobody lists are attached
 *   where they belong (controls into the rules card, connect buttons into
 *   the apps card, the hire and change buttons into the button row);
 * - always has "Ask me before sending anything" bound to /rules/askFirst,
 *   defaulting to on, and exactly one hire button and one change button;
 * - has at most LIMITS.maxElements elements (the buttons and that toggle
 *   are always kept);
 * - cannot reach an object prototype (ids, prop keys and state keys named
 *   `__proto__`, `constructor` or `prototype` are dropped).
 *
 * `ask` buttons become plain `refine`: the owner writes the change, the
 * model's suggested wording is never sent on their behalf.
 *
 * Returns null when nothing renderable came back. The server applies the
 * same "anything renderable" rule before retrying a reply
 * (services/employees/setup_reply.py).
 */

import {
  ASK_FIRST_LABEL,
  LIMITS,
  STATE_PATHS,
  actionOf,
  isComponentType,
  isContainer,
  isControl,
  type ComponentType,
} from './catalog';
import { bindingPath, getPath, isForbiddenKey, sanitizeState, setPath, type UiState } from './expressions';

export interface SpecElement {
  type: ComponentType;
  /** Props as written (expressions unresolved). */
  props: Record<string, unknown>;
  children: string[];
  visible?: unknown;
}

export interface NormalizedSpec {
  root: string;
  state: UiState;
  elements: Record<string, SpecElement>;
  /** Element ids in render order (depth first), for the reveal. */
  order: string[];
}

const META_KEYS = new Set(['type', 'props', 'children', 'visible', 'watch', 'id']);
const ID_PATTERN = /^[A-Za-z0-9_.:-]+$/;
const MAX_RAW_ELEMENTS = 64;
const RULES_TITLE = /rule|setting|prefer|control|how/i;
const APPS_TITLE = /app|connect|need|check/i;
const ASK_FIRST_WORDING = /\bask\b.*\b(before|first)\b/i;

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

/** Buttons need a label and a known action; an agent card needs a name. */
function keep(element: SpecElement): boolean {
  if (element.type === 'Button') {
    const action = actionOf(element.props.action);
    if (!text(element.props.label) || !action) return false;
    element.props.action = action;
    if (action === 'refine') delete element.props.actionParams;
    else if ('actionParams' in element.props) element.props.actionParams = sanitizeState(element.props.actionParams, LIMITS.maxText);
  }
  if (element.type === 'AgentCard' && !text(element.props.name)) return false;
  return true;
}

function collect(raw: Record<string, unknown>): Elements {
  const out: Elements = new Map();
  for (const [id, value] of Object.entries(raw)) {
    if (out.size >= MAX_RAW_ELEMENTS) break;
    if (!usableId(id) || !isRecord(value) || !isComponentType(value.type)) continue;
    const props: Record<string, unknown> = {};
    for (const key of Object.keys(value)) {
      if (!META_KEYS.has(key) && !isForbiddenKey(key)) props[key] = value[key];
    }
    if (isRecord(value.props)) {
      for (const key of Object.keys(value.props)) {
        if (!isForbiddenKey(key)) props[key] = value.props[key];
      }
    }
    const children = Array.isArray(value.children)
      ? value.children.filter((child): child is string => typeof child === 'string')
      : [];
    const element: SpecElement = { type: value.type, props, children };
    if ('visible' in value) element.visible = value.visible;
    if (keep(element)) out.set(id, element);
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

function isHorizontalStack(element: SpecElement): boolean {
  return element.type === 'Stack' && element.props.direction === 'horizontal';
}

function buttonAction(element: SpecElement | undefined): string | null {
  return element?.type === 'Button' ? String(element.props.action) : null;
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

function removeEverywhere(elements: Elements, id: string): void {
  elements.delete(id);
  for (const element of elements.values()) {
    const index = element.children.indexOf(id);
    if (index !== -1) element.children.splice(index, 1);
  }
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
  const sorted = { actions: [] as string[], controls: [] as string[], connects: [] as string[], others: [] as string[] };
  const sort = (id: string) => {
    const element = elements.get(id)!;
    const action = buttonAction(element);
    if (action === 'hire_employee' || action === 'refine') sorted.actions.push(id);
    else if (isControl(element.type)) sorted.controls.push(id);
    else if (action === 'connect_app') sorted.connects.push(id);
    else sorted.others.push(id);
  };
  const attach = (host: string, ids: string[]) => {
    if (ids.length === 0) return;
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
  if (sorted.actions.length > 0) {
    const row = depthFirst(elements, root).find((id) => {
      const element = elements.get(id)!;
      return isHorizontalStack(element) && element.children.length === 0;
    });
    let host = row;
    if (!host) {
      host = freshId(elements, '__acts');
      elements.set(host, { type: 'Stack', props: { direction: 'horizontal', gap: 'md' }, children: [] });
      attach(root, [host]);
    }
    attach(host, sorted.actions);
  }

  for (const id of [...elements.keys()]) if (!placed.has(id)) elements.delete(id);

  // One hire button and one change button: the first of each, depth first.
  for (const action of ['hire_employee', 'refine']) {
    const buttons = depthFirst(elements, root).filter((id) => buttonAction(elements.get(id)) === action);
    buttons.slice(1).forEach((id) => removeEverywhere(elements, id));
  }

  // "Ask me before sending anything" is always there, on by default.
  const order = depthFirst(elements, root);
  let askFirst = order.find((id) => {
    const element = elements.get(id)!;
    return element.type === 'Toggle' && bindingPath(element.props.value) === STATE_PATHS.askFirst;
  });
  if (!askFirst) {
    const worded = order.find((id) => {
      const element = elements.get(id)!;
      return element.type === 'Toggle' && ASK_FIRST_WORDING.test(text(element.props.label));
    });
    if (worded) {
      const element = elements.get(worded)!;
      const previous = getPath(state, bindingPath(element.props.value));
      element.props.value = { $bindState: STATE_PATHS.askFirst };
      if (typeof previous === 'boolean') state = setPath(state, STATE_PATHS.askFirst, previous);
      askFirst = worded;
    }
  }
  if (typeof getPath(state, STATE_PATHS.askFirst) !== 'boolean') state = setPath(state, STATE_PATHS.askFirst, true);

  const agent = order.map((id) => elements.get(id)!).find((element) => element.type === 'AgentCard');
  const name = text(agent?.props.name);
  const hasHire = order.some((id) => buttonAction(elements.get(id)) === 'hire_employee');
  const hasChange = order.some((id) => buttonAction(elements.get(id)) === 'refine');
  const actionRow = () =>
    depthFirst(elements, root).find((id) => {
      const element = elements.get(id)!;
      return isHorizontalStack(element) && element.children.some((child) => elements.get(child)?.type === 'Button');
    });
  if (!hasHire || !hasChange) {
    const added: string[] = [];
    if (!hasHire) {
      const id = freshId(elements, '__hire');
      const apps = Array.isArray(agent?.props.apps) ? agent!.props.apps.filter((app) => typeof app === 'string') : [];
      elements.set(id, {
        type: 'Button',
        props: {
          label: name ? `Hire ${name}` : 'Hire them',
          variant: 'primary',
          action: 'hire_employee',
          actionParams: { name: name || undefined, role: text(agent?.props.role) || undefined, apps },
        },
        children: [],
      });
      added.push(id);
    }
    if (!hasChange) {
      const id = freshId(elements, '__edit');
      elements.set(id, { type: 'Button', props: { label: 'Change something', variant: 'secondary', action: 'refine' }, children: [] });
      added.push(id);
    }
    let host = actionRow();
    if (!host) {
      host = freshId(elements, '__acts');
      elements.set(host, { type: 'Stack', props: { direction: 'horizontal', gap: 'md' }, children: [] });
      attach(root, [host]);
    }
    attach(host, added);
  }

  if (!askFirst) {
    const cardsNow = depthFirst(elements, root).filter((id) => elements.get(id)!.type === 'Card');
    let host = titled(cardsNow, /rule/i);
    if (!host) {
      host = freshId(elements, '__rules');
      elements.set(host, { type: 'Card', props: { title: 'Ground rules' }, children: [] });
      // Before the button row, which stays last.
      const rootChildren = elements.get(root)!.children;
      const row = actionRow();
      const at = row ? rootChildren.indexOf(row) : -1;
      rootChildren.splice(at === -1 ? rootChildren.length : at, 0, host);
      placed.add(host);
    }
    askFirst = freshId(elements, '__askFirst');
    elements.set(askFirst, {
      type: 'Toggle',
      props: { label: ASK_FIRST_LABEL, value: { $bindState: STATE_PATHS.askFirst } },
      children: [],
    });
    attach(host, [askFirst]);
  }

  // Cap the size, always keeping the buttons, the toggle and their parents.
  const full = depthFirst(elements, root);
  const parentOf = new Map<string, string>();
  for (const id of full) for (const child of elements.get(id)!.children) parentOf.set(child, id);
  const mustKeep = new Set<string>([root]);
  const keepWithAncestors = (id: string | undefined) => {
    for (let at = id; at !== undefined; at = parentOf.get(at)) mustKeep.add(at);
  };
  keepWithAncestors(askFirst);
  full.filter((id) => ['hire_employee', 'refine'].includes(buttonAction(elements.get(id)) ?? '')).forEach(keepWithAncestors);
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

  const record: Record<string, SpecElement> = Object.create(null);
  for (const [id, element] of elements) record[id] = element;
  return { root, state, elements: record, order: depthFirst(elements, root) };
}
