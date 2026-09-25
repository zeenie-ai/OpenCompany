/**
 * Dynamic values in a setup screen's props, evaluated against its UI state:
 *
 * - `{"$state": "/path"}`             the value at a path
 * - `{"$bindState": "/path"}`         the same, for a control that writes it back
 * - `{"$template": "Reports ${/freq}"}` text with state values filled in
 * - `{"$cond": C, "$then": a, "$else": b}` a choice between two values
 * - conditions C (and an element's `visible`): `{"$state": "/path"}`, with
 *   optional `"eq"` / `"neq"` and `"not": true`; an array means all of them.
 *
 * Paths are `/`-separated keys. A path through `__proto__`, `constructor`
 * or `prototype` is refused, so model-written paths can never reach an
 * object's prototype. Writes copy along the path and keep arrays as
 * arrays.
 */

export type UiState = Record<string, unknown>;

const FORBIDDEN_KEYS = new Set(['__proto__', 'constructor', 'prototype']);
const MAX_DEPTH = 8;
const MAX_PATH_KEYS = 6;

export function isForbiddenKey(key: string): boolean {
  return FORBIDDEN_KEYS.has(key);
}

/** The keys of a path, or null when the path is not a usable string. */
export function pathKeys(path: unknown): string[] | null {
  if (typeof path !== 'string') return null;
  const keys = path.split('/').filter(Boolean);
  if (keys.length === 0 || keys.length > MAX_PATH_KEYS || keys.some(isForbiddenKey)) return null;
  return keys;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function childOf(value: unknown, key: string): unknown {
  if (Array.isArray(value)) {
    const index = Number(key);
    return Number.isInteger(index) && index >= 0 ? value[index] : undefined;
  }
  if (isRecord(value) && Object.prototype.hasOwnProperty.call(value, key)) return value[key];
  return undefined;
}

export function getPath(state: unknown, path: unknown): unknown {
  const keys = pathKeys(path);
  if (!keys) return undefined;
  return keys.reduce<unknown>((value, key) => childOf(value, key), state);
}

/** A copy of `state` with `value` at `path`; `state` itself is unchanged.
 *  An unusable path returns `state` as it was. */
export function setPath(state: UiState, path: unknown, value: unknown): UiState {
  const keys = pathKeys(path);
  if (!keys) return state;
  const write = (node: unknown, index: number): unknown => {
    const key = keys[index];
    const last = index === keys.length - 1;
    if (Array.isArray(node)) {
      const at = Number(key);
      if (!Number.isInteger(at) || at < 0 || at > node.length) return node;
      const copy = node.slice();
      copy[at] = last ? value : write(node[at], index + 1);
      return copy;
    }
    const base = isRecord(node) ? node : {};
    return { ...base, [key]: last ? value : write(childOf(base, key), index + 1) };
  };
  return write(state, 0) as UiState;
}

/** The path a control writes to (`{"$bindState": "/path"}`), or null. */
export function bindingPath(raw: unknown): string | null {
  if (!isRecord(raw) || !('$bindState' in raw)) return null;
  return pathKeys(raw.$bindState) ? String(raw.$bindState) : null;
}

export function evalCondition(condition: unknown, state: UiState, depth = 0): boolean {
  if (depth > MAX_DEPTH) return false;
  if (Array.isArray(condition)) return condition.every((item) => evalCondition(item, state, depth + 1));
  if (condition === null || condition === undefined) return true;
  if (!isRecord(condition)) return Boolean(condition);
  const value = getPath(state, condition.$state);
  let result: boolean;
  if ('eq' in condition) result = value === resolveValue(condition.eq, state, depth + 1);
  else if ('neq' in condition) result = value !== resolveValue(condition.neq, state, depth + 1);
  else result = Boolean(value);
  return condition.not ? !result : result;
}

export function resolveValue(value: unknown, state: UiState, depth = 0): unknown {
  if (depth > MAX_DEPTH) return undefined;
  if (Array.isArray(value)) return value.map((item) => resolveValue(item, state, depth + 1));
  if (!isRecord(value)) return value;
  if ('$bindState' in value) return getPath(state, value.$bindState);
  if ('$cond' in value) {
    return evalCondition(value.$cond, state, depth + 1)
      ? resolveValue(value.$then, state, depth + 1)
      : resolveValue(value.$else, state, depth + 1);
  }
  if ('$state' in value) return getPath(state, value.$state);
  if ('$template' in value) {
    return String(value.$template ?? '').replace(/\$\{([^}]+)\}/g, (_match, path: string) => {
      const found = getPath(state, path.trim());
      return found === null || found === undefined || typeof found === 'object' ? '' : String(found);
    });
  }
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(value)) {
    if (!isForbiddenKey(key)) out[key] = resolveValue(value[key], state, depth + 1);
  }
  return out;
}

/** A plain JSON copy of a model's initial state: no prototype keys, depth
 *  and size bounded, strings clamped. */
export function sanitizeState(raw: unknown, maxString: number, depth = 0): unknown {
  if (depth > 4) return undefined;
  if (typeof raw === 'string') return raw.slice(0, maxString);
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : undefined;
  if (typeof raw === 'boolean' || raw === null) return raw;
  if (Array.isArray(raw)) {
    return raw.slice(0, 20).map((item) => sanitizeState(item, maxString, depth + 1)).filter((item) => item !== undefined);
  }
  if (isRecord(raw)) {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(raw).slice(0, 40)) {
      if (isForbiddenKey(key)) continue;
      const value = sanitizeState(raw[key], maxString, depth + 1);
      if (value !== undefined) out[key] = value;
    }
    return out;
  }
  return undefined;
}
