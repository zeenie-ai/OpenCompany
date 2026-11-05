/**
 * A generated UI made ready for json-render. The server checked the spec
 * already (server/services/genui/spec.py); this side sanitizes it again
 * (lib/jsonRender/sanitize.ts, with the chat's limits), leaves out
 * components the chat catalogue does not have, and tags each Button's press
 * with the element and its label (`PRESS_ELEMENT`, `PRESS_LABEL`), because
 * json-render hands an action handler the resolved params only: the `ask`
 * action needs the label to tell whether its text is what the button says,
 * and any other action is sent back to the employee naming the element.
 *
 * Pure, and free of json-render at run time.
 */

import type { ActionBinding, Spec, UIElement } from '@json-render/core';
import { DEFAULT_SANITIZE_LIMITS, sanitizeSpec, sanitizeState, type SanitizeLimits } from '@/lib/jsonRender/sanitize';
import { limits } from '../../../../../server/config/chat_genui_catalog.json';
import { isChatComponentType } from './catalog';

/** The keys a pressed button adds to its action's params. */
export const PRESS_ELEMENT = '__oc_element';
export const PRESS_LABEL = '__oc_label';

/** The server's limits (server/config/chat_genui_catalog.json). */
export const CHAT_SANITIZE_LIMITS: SanitizeLimits = {
  ...DEFAULT_SANITIZE_LIMITS,
  maxElements: limits.max_elements,
  maxChildren: limits.max_children,
  maxIdLength: limits.max_id_length,
  maxPathSegments: limits.max_path_segments,
};

function tagged(binding: ActionBinding, id: string, label: unknown): ActionBinding {
  return { ...binding, params: { ...(binding.params ?? {}), [PRESS_ELEMENT]: id, [PRESS_LABEL]: label ?? '' } };
}

function withPressIdentity(id: string, element: UIElement): UIElement {
  const press = element.on?.press;
  if (element.type !== 'Button' || !press) return element;
  const label = element.props.label;
  const bindings = Array.isArray(press) ? press.map((binding) => tagged(binding, id, label)) : tagged(press, id, label);
  return { ...element, on: { ...element.on, press: bindings } };
}

/**
 * The spec to render, or null while it has no root element (a UI still
 * arriving starts empty). Children that name a dropped component are
 * removed; children not here yet stay (they may still be on their way).
 */
export function prepareChatSpec(raw: unknown): Spec | null {
  const spec = sanitizeSpec(raw, CHAT_SANITIZE_LIMITS);
  if (!spec) return null;
  const dropped = new Set<string>();
  const kept: Record<string, UIElement> = Object.create(null);
  for (const [id, element] of Object.entries(spec.elements)) {
    if (isChatComponentType(element.type)) kept[id] = element;
    else dropped.add(id);
  }
  if (!Object.prototype.hasOwnProperty.call(kept, spec.root)) return null;
  const elements: Record<string, UIElement> = Object.create(null);
  for (const [id, element] of Object.entries(kept)) {
    const children = element.children?.filter((child) => !dropped.has(child));
    const clean = children && children.length !== element.children?.length ? { ...element, children } : element;
    elements[id] = withPressIdentity(id, clean);
  }
  return { root: spec.root, state: spec.state, elements };
}

/** What the owner last set, as the saved reply holds it, made ready the way a
 *  spec's own state is: plain data, without prototype keys. */
export function prepareChatState(raw: unknown): Record<string, unknown> {
  return sanitizeState(raw, CHAT_SANITIZE_LIMITS);
}

/** A press's params as the employee should see them: without the tags. */
export function untagged(params: Record<string, unknown>): {
  elementId: string;
  label: string;
  params: Record<string, unknown>;
} {
  const { [PRESS_ELEMENT]: element, [PRESS_LABEL]: label, ...rest } = params;
  return {
    elementId: typeof element === 'string' ? element : '',
    label: typeof label === 'string' ? label : label === undefined || label === null ? '' : String(label),
    params: rest,
  };
}
