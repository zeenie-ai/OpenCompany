/**
 * Reading a model's setup reply: `{"text": string, "spec": UISpec}`, asked
 * for as bare JSON but often wrapped in code fences, preceded by chatter,
 * or cut off mid-object when the model ran out of tokens.
 *
 * `parseReply` takes the first `{` to the last `}`; failing that it repairs
 * a truncated object by cutting back to the last complete value and
 * closing what is still open. Replies are read up to 64K characters.
 *
 * The server runs the same salvage rules to decide whether to retry
 * (services/employees/setup_reply.py); both sides are checked against the
 * shared corpus in genui/__fixtures__/replies.json.
 */

import { LIMITS } from './catalog';

export interface ParsedReply {
  /** The model's one-sentence introduction. */
  text: string;
  /** The raw setup-screen spec, not yet normalized. */
  spec: unknown;
  /** Nothing usable came back (no spec). `text` may still be set. */
  failed: boolean;
}

const MAX_REPAIR_CUTS = 80;

function asReply(value: unknown): { text: string; spec: unknown } | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (!record.text && !record.spec) return null;
  const spec = record.spec && typeof record.spec === 'object' ? record.spec : null;
  return { text: typeof record.text === 'string' ? record.text : String(record.text ?? ''), spec };
}

/** Scan for open brackets and whether the text ends inside a string. */
function openState(source: string): { stack: string[]; inString: boolean } {
  const stack: string[] = [];
  let inString = false;
  let escaped = false;
  for (const ch of source) {
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === '{' || ch === '[') stack.push(ch);
    else if (ch === '}' || ch === ']') stack.pop();
  }
  return { stack, inString };
}

/** Parse a JSON object that was cut off: try every point where a value
 *  ended, latest first, closing the brackets still open. */
export function repairJson(source: string): unknown {
  const cuts: number[] = [];
  let inString = false;
  let escaped = false;
  for (let i = 0; i < source.length; i++) {
    const ch = source[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === ',') cuts.push(i);
    else if (ch === '}' || ch === ']') cuts.push(i + 1);
  }
  for (let k = cuts.length - 1, tried = 0; k >= 0 && tried < MAX_REPAIR_CUTS; k--, tried++) {
    const prefix = source.slice(0, cuts[k]).replace(/,\s*$/, '');
    const state = openState(prefix);
    if (state.inString) continue;
    const closers = state.stack
      .slice()
      .reverse()
      .map((open) => (open === '{' ? '}' : ']'))
      .join('');
    try {
      return JSON.parse(prefix + closers);
    } catch {
      // Try the next earlier cut.
    }
  }
  return null;
}

export function parseReply(raw: unknown): ParsedReply {
  const reply = String(raw ?? '').slice(0, LIMITS.maxReplyChars);
  const unfenced = reply.replace(/```(?:json)?/gi, '');
  const start = unfenced.indexOf('{');
  if (start >= 0) {
    const body = unfenced.slice(start);
    const end = body.lastIndexOf('}');
    if (end > 0) {
      try {
        const whole = asReply(JSON.parse(body.slice(0, end + 1)));
        if (whole) return { ...whole, failed: !whole.spec };
      } catch {
        // Fall through to the repair.
      }
    }
    const repaired = asReply(repairJson(body));
    if (repaired) return { ...repaired, failed: !repaired.spec };
  }
  // No usable object: keep the introduction if one is visible.
  const quoted = /"text"\s*:\s*"((?:[^"\\]|\\.)*)"/.exec(reply);
  if (quoted) {
    try {
      return { text: JSON.parse(`"${quoted[1]}"`) as string, spec: null, failed: true };
    } catch {
      // Unreadable escape sequence.
    }
  }
  if (start < 0 && reply.trim() && !/[{}[\]]/.test(reply)) {
    return { text: reply.trim(), spec: null, failed: true };
  }
  return { text: '', spec: null, failed: true };
}
