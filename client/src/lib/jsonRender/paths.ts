/**
 * State paths in a generated UI: JSON Pointers (RFC 6901) such as
 * "/rules/askFirst", the form json-render reads.
 *
 * json-render 0.21.4 reads own properties only and its patch helpers skip
 * `__proto__`, but its state store writes through any segment
 * (`immutableSetByPath`) and nothing refuses `constructor` or `prototype`,
 * so a path written by a model could still reach a prototype. Every path a
 * spec, a patch or a control uses goes through `parseStatePath` first. It
 * refuses `__proto__`, `constructor` and `prototype` segments (after
 * unescaping `~1` and `~0`), paths with no segments, and paths deeper than
 * MAX_PATH_SEGMENTS; empty segments ("/a//b/") are skipped, as the setup
 * screen always has. `readPath` and `writePath` apply the same rule, read
 * own properties only, and copy along the path on a write, keeping arrays
 * as arrays.
 *
 * Nothing here imports json-render at run time, so modules Home loads
 * eagerly can use it without pulling the renderer into Home's first chunk.
 */

export const FORBIDDEN_SEGMENTS: ReadonlySet<string> = new Set(['__proto__', 'constructor', 'prototype']);

/** The deepest state path a generated UI may use. */
export const MAX_PATH_SEGMENTS = 6;

export function isForbiddenSegment(segment: string): boolean {
  return FORBIDDEN_SEGMENTS.has(segment);
}

export function unescapePointer(token: string): string {
  return token.replace(/~1/g, '/').replace(/~0/g, '~');
}

export function escapePointer(token: string): string {
  return token.replace(/~/g, '~0').replace(/\//g, '~1');
}

/** The segments of a usable state path, unescaped; null when it is not one.
 *  The leading "/" is optional, as it is for json-render. */
export function parseStatePath(path: unknown, maxSegments: number = MAX_PATH_SEGMENTS): string[] | null {
  if (typeof path !== 'string') return null;
  const segments = path
    .split('/')
    .filter(Boolean)
    .map(unescapePointer);
  if (segments.length === 0 || segments.length > maxSegments || segments.some(isForbiddenSegment)) return null;
  return segments;
}

/** A usable state path in its one canonical form ("/a/b"), or null. */
export function canonicalStatePath(path: unknown, maxSegments: number = MAX_PATH_SEGMENTS): string | null {
  const segments = parseStatePath(path, maxSegments);
  return segments ? joinStatePath(segments) : null;
}

export function joinStatePath(segments: readonly string[]): string {
  return `/${segments.map(escapePointer).join('/')}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The path a control writes to (`{"$bindState": "/path"}`), or null. */
export function bindingPath(raw: unknown): string | null {
  if (!isRecord(raw) || !('$bindState' in raw)) return null;
  return parseStatePath(raw.$bindState) ? String(raw.$bindState) : null;
}

function childOf(value: unknown, key: string): unknown {
  if (Array.isArray(value)) {
    const index = Number(key);
    return Number.isInteger(index) && index >= 0 ? value[index] : undefined;
  }
  if (isRecord(value) && Object.prototype.hasOwnProperty.call(value, key)) return value[key];
  return undefined;
}

/** The value at a path (own properties only); undefined when the path is
 *  unusable or leads nowhere. */
export function readPath(state: unknown, path: unknown): unknown {
  const segments = parseStatePath(path);
  if (!segments) return undefined;
  return segments.reduce<unknown>((value, key) => childOf(value, key), state);
}

/** A copy of `state` with `value` at `path`; `state` itself is unchanged.
 *  An unusable path, or an array index past the end, returns `state` as it
 *  was. */
export function writePath<T extends Record<string, unknown>>(state: T, path: unknown, value: unknown): T {
  const segments = parseStatePath(path);
  if (!segments) return state;
  let refused = false;
  const write = (node: unknown, index: number): unknown => {
    const key = segments[index];
    const last = index === segments.length - 1;
    if (Array.isArray(node)) {
      const at = Number(key);
      if (!Number.isInteger(at) || at < 0 || at > node.length) {
        refused = true;
        return node;
      }
      const copy = node.slice();
      copy[at] = last ? value : write(node[at], index + 1);
      return copy;
    }
    const base = isRecord(node) ? node : {};
    return { ...base, [key]: last ? value : write(childOf(base, key), index + 1) };
  };
  const next = write(state, 0) as T;
  return refused ? state : next;
}
