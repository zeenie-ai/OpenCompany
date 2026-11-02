/**
 * Making a generated UI safe to hand to json-render.
 *
 * json-render 0.21.4 does not guard every state path (its state store writes
 * through any segment), does not check props against the catalog, and runs element
 * features a model should never reach: `watch` (actions fired by state
 * changes), `repeat`, `slots`, `$computed`, and an action binding's
 * `confirm`, `onSuccess` and `onError` (the last two set state or run
 * further actions). So before a spec reaches the renderer:
 *
 * - an element keeps its `type`, `props`, `children`, `visible` and `on`
 *   only, and an action binding its `action` and `params` only;
 * - `__proto__`, `constructor` and `prototype` are refused everywhere: as
 *   ids, object keys, and segments of `$state` / `$bindState` paths,
 *   `$template` placeholders, `visible` conditions and action params
 *   (paths.ts holds the rule); every path that survives is rewritten in one
 *   canonical form;
 * - `$computed` is dropped, and so are `$item`, `$index` and `$bindItem`,
 *   which mean nothing once `repeat` is gone;
 * - sizes are capped: elements, children, nesting depth, keys, array and
 *   string lengths.
 *
 * What an expression that is refused becomes: a prop or param is dropped
 * (as if never written); a condition reads as false (the element hides).
 * Conditions written as an array become `{"$and": [...]}`, which
 * json-render evaluates the same way but also accepts plain booleans in.
 *
 * Props are not checked against the catalog here: the renderer's guard
 * reads each one with the component's own schema (guard.tsx). Unknown
 * component types are the caller's to drop, as is building the tree.
 */

import type { ActionBinding, Spec, UIElement, VisibilityCondition } from '@json-render/core';
import { MAX_PATH_SEGMENTS, canonicalStatePath, isForbiddenSegment } from './paths';

export interface SanitizeLimits {
  /** Elements kept from a spec, in the order written. */
  maxElements: number;
  /** Child ids kept per element. */
  maxChildren: number;
  maxIdLength: number;
  /** How deep a value nests before the rest is dropped. */
  maxDepth: number;
  /** Items kept per array, keys per object. */
  maxArray: number;
  maxKeys: number;
  /** Characters kept per string. */
  maxString: number;
  maxPathSegments: number;
}

export const DEFAULT_SANITIZE_LIMITS: SanitizeLimits = {
  maxElements: 64,
  maxChildren: 64,
  maxIdLength: 40,
  maxDepth: 8,
  maxArray: 50,
  maxKeys: 40,
  maxString: 4000,
  maxPathSegments: MAX_PATH_SEGMENTS,
};

const ID_PATTERN = /^[A-Za-z0-9_.:-]+$/;
const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
const NAME_MAX = 64;
/** Expressions that only work inside `repeat` or call app code. */
const DROPPED_EXPRESSIONS = ['$computed', '$item', '$index', '$bindItem'] as const;
const COMPARISONS = ['eq', 'neq'] as const;
const ORDERINGS = ['gt', 'gte', 'lt', 'lte'] as const;
/** Bindings kept per event. */
const MAX_BINDINGS = 4;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** An element id the renderer can key on: short, plain characters, never a prototype key. */
export function isUsableId(id: unknown, limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS): id is string {
  return typeof id === 'string' && id.length <= limits.maxIdLength && ID_PATTERN.test(id) && !isForbiddenSegment(id);
}

/** A component type, action or event name: an identifier, never a prototype key. */
export function isUsableName(name: unknown): name is string {
  return typeof name === 'string' && name.length <= NAME_MAX && NAME_PATTERN.test(name) && !isForbiddenSegment(name);
}

function clip(text: string, limits: SanitizeLimits): string {
  return text.length > limits.maxString ? text.slice(0, limits.maxString) : text;
}

/** Plain JSON data (initial state, a state write): prototype keys dropped,
 *  sizes capped, non-JSON values (NaN, functions) dropped. */
export function sanitizeJson(value: unknown, limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS, depth = 0): unknown {
  if (depth > limits.maxDepth) return undefined;
  if (value === null || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (typeof value === 'string') return clip(value, limits);
  if (Array.isArray(value)) {
    const out: unknown[] = [];
    for (const item of value.slice(0, limits.maxArray)) {
      const clean = sanitizeJson(item, limits, depth + 1);
      if (clean !== undefined) out.push(clean);
    }
    return out;
  }
  if (!isRecord(value)) return undefined;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(value).slice(0, limits.maxKeys)) {
    if (isForbiddenSegment(key)) continue;
    const clean = sanitizeJson(value[key], limits, depth + 1);
    if (clean !== undefined) out[key] = clean;
  }
  return out;
}

/** A spec's initial state: always an object. */
export function sanitizeState(raw: unknown, limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS): Record<string, unknown> {
  const clean = sanitizeJson(raw, limits);
  return isRecord(clean) ? clean : {};
}

function pathExpression(key: '$state' | '$bindState', path: unknown, limits: SanitizeLimits) {
  const canonical = canonicalStatePath(path, limits.maxPathSegments);
  return canonical ? { [key]: canonical } : undefined;
}

/** `${path}` placeholders rewritten canonically; one that reads a refused
 *  path becomes empty text, as an unset value would. */
function sanitizeTemplate(template: string, limits: SanitizeLimits): string {
  return clip(template, limits).replace(/\$\{([^}]+)\}/g, (_match, raw: string) => {
    const path = canonicalStatePath(raw.trim(), limits.maxPathSegments);
    return path ? `\${${path}}` : '';
  });
}

/** A value that may hold json-render expressions (props, action params). */
export function sanitizeExpressions(value: unknown, limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS, depth = 0): unknown {
  if (depth > limits.maxDepth) return undefined;
  if (Array.isArray(value)) {
    const out: unknown[] = [];
    for (const item of value.slice(0, limits.maxArray)) {
      const clean = sanitizeExpressions(item, limits, depth + 1);
      if (clean !== undefined) out.push(clean);
    }
    return out;
  }
  if (!isRecord(value)) return sanitizeJson(value, limits, depth);
  if ('$state' in value) return pathExpression('$state', value.$state, limits);
  if ('$bindState' in value) return pathExpression('$bindState', value.$bindState, limits);
  if (DROPPED_EXPRESSIONS.some((key) => key in value)) return undefined;
  if ('$cond' in value) {
    const branch = (raw: unknown) => sanitizeExpressions(raw, limits, depth + 1) ?? null;
    return {
      $cond: sanitizeCondition(value.$cond, limits, depth + 1) ?? true,
      $then: branch(value.$then),
      $else: branch(value.$else),
    };
  }
  if ('$template' in value) {
    return typeof value.$template === 'string' ? { $template: sanitizeTemplate(value.$template, limits) } : undefined;
  }
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(value).slice(0, limits.maxKeys)) {
    if (isForbiddenSegment(key)) continue;
    const clean = sanitizeExpressions(value[key], limits, depth + 1);
    if (clean !== undefined) out[key] = clean;
  }
  return out;
}

function comparisonValue(value: unknown, limits: SanitizeLimits, depth: number): unknown {
  if (isRecord(value) && '$state' in value) return pathExpression('$state', value.$state, limits) ?? null;
  return sanitizeJson(value, limits, depth + 1) ?? null;
}

/**
 * A `visible` condition (or a `$cond`). undefined means "no condition"
 * (always shown). One that reads a refused path, or that only works inside
 * `repeat` ($item, $index), or that has no recognisable shape, reads as
 * false.
 */
export function sanitizeCondition(
  condition: unknown,
  limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS,
  depth = 0,
): VisibilityCondition | undefined {
  if (condition === undefined || condition === null) return undefined;
  if (typeof condition === 'boolean') return condition;
  if (depth > limits.maxDepth) return false;
  const all = (items: unknown[]) =>
    items.slice(0, limits.maxArray).map((item) => sanitizeCondition(item, limits, depth + 1) ?? true);
  if (Array.isArray(condition)) return { $and: all(condition) };
  if (!isRecord(condition)) return Boolean(condition);
  if ('$and' in condition) return Array.isArray(condition.$and) ? { $and: all(condition.$and) } : false;
  if ('$or' in condition) return Array.isArray(condition.$or) ? { $or: all(condition.$or) } : false;
  if (!('$state' in condition)) return false;
  const path = canonicalStatePath(condition.$state, limits.maxPathSegments);
  if (!path) return false;
  const out: Record<string, unknown> = { $state: path };
  for (const op of COMPARISONS) {
    if (condition[op] !== undefined) out[op] = comparisonValue(condition[op], limits, depth);
  }
  for (const op of ORDERINGS) {
    const value = condition[op];
    if (typeof value === 'number' && Number.isFinite(value)) out[op] = value;
    else if (isRecord(value) && '$state' in value) {
      const bound = pathExpression('$state', value.$state, limits);
      if (bound) out[op] = bound;
    }
  }
  if (condition.not) out.not = true;
  return out as VisibilityCondition;
}

/** An action binding: a usable action name and its params, nothing else. */
export function sanitizeActionBinding(raw: unknown, limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS): ActionBinding | null {
  if (!isRecord(raw) || !isUsableName(raw.action)) return null;
  const binding: ActionBinding = { action: raw.action };
  if (raw.params !== undefined) {
    const params = sanitizeExpressions(raw.params, limits, 1);
    if (isRecord(params) && Object.keys(params).length > 0) binding.params = params;
  }
  return binding;
}

function sanitizeBindings(raw: unknown, limits: SanitizeLimits): ActionBinding | ActionBinding[] | undefined {
  if (!Array.isArray(raw)) return sanitizeActionBinding(raw, limits) ?? undefined;
  const list = raw
    .slice(0, MAX_BINDINGS)
    .map((binding) => sanitizeActionBinding(binding, limits))
    .filter((binding): binding is ActionBinding => binding !== null);
  return list.length > 0 ? list : undefined;
}

/** An element's `on`: event name -> binding(s). */
export function sanitizeOn(
  raw: unknown,
  limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS,
): Record<string, ActionBinding | ActionBinding[]> | undefined {
  if (!isRecord(raw)) return undefined;
  const out: Record<string, ActionBinding | ActionBinding[]> = {};
  for (const event of Object.keys(raw).slice(0, limits.maxKeys)) {
    if (!isUsableName(event)) continue;
    const bindings = sanitizeBindings(raw[event], limits);
    if (bindings) out[event] = bindings;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

/** Child ids: usable, each once, capped. */
export function sanitizeChildren(raw: unknown, limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS): string[] {
  if (!Array.isArray(raw)) return [];
  const out: string[] = [];
  for (const child of raw) {
    if (out.length >= limits.maxChildren) break;
    if (isUsableId(child, limits) && !out.includes(child)) out.push(child);
  }
  return out;
}

/** One element: type, props, children, visible and on; null when its type
 *  is not a usable name. */
export function sanitizeElement(raw: unknown, limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS): UIElement | null {
  if (!isRecord(raw) || !isUsableName(raw.type)) return null;
  const props = isRecord(raw.props) ? sanitizeExpressions(raw.props, limits) : {};
  const element: UIElement = { type: raw.type, props: isRecord(props) ? props : {} };
  if (raw.children !== undefined) element.children = sanitizeChildren(raw.children, limits);
  const visible = sanitizeCondition(raw.visible, limits);
  if (visible !== undefined) element.visible = visible;
  const on = sanitizeOn(raw.on, limits);
  if (on) element.on = on;
  return element;
}

/** A whole flat spec `{root, state, elements}`; null when it has no usable
 *  root id or no elements object. Elements keep the order written. */
export function sanitizeSpec(raw: unknown, limits: SanitizeLimits = DEFAULT_SANITIZE_LIMITS): Spec | null {
  if (!isRecord(raw) || !isRecord(raw.elements) || !isUsableId(raw.root, limits)) return null;
  // No prototype: ids are looked up here as keys.
  const elements: Record<string, UIElement> = Object.create(null);
  let count = 0;
  for (const id of Object.keys(raw.elements)) {
    if (count >= limits.maxElements) break;
    if (!isUsableId(id, limits)) continue;
    const element = sanitizeElement(raw.elements[id], limits);
    if (!element) continue;
    elements[id] = element;
    count++;
  }
  return { root: raw.root, state: sanitizeState(raw.state, limits), elements };
}
