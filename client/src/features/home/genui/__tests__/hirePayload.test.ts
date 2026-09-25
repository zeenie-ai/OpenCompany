import { describe, expect, it } from 'vitest';
import corpus from '../__fixtures__/replies.json';
import { STATE_PATHS } from '../catalog';
import { resolveValue, setPath } from '../expressions';
import { HIRE_PAYLOAD_KEYS, buildHirePayload, fitsHireLimit } from '../hirePayload';
import { normalizeSpec, type NormalizedSpec } from '../normalize';
import { parseReply } from '../parse';

const replies = corpus as unknown as { name: string; reply: string }[];

function specOf(name: string): NormalizedSpec {
  const parsed = parseReply(replies.find((c) => c.name === name)!.reply);
  return normalizeSpec(parsed.spec)!;
}

function hireParams(spec: NormalizedSpec, state = spec.state) {
  const id = spec.order.find((key) => spec.elements[key].props.action === 'hire_employee')!;
  return resolveValue(spec.elements[id].props.actionParams ?? {}, state) as Record<string, unknown>;
}

describe('buildHirePayload', () => {
  const spec = specOf('clean minified reply');

  it('reads the employee, their routine and the owner’s settings off the screen', () => {
    const payload = buildHirePayload({
      spec,
      state: spec.state,
      params: hireParams(spec),
      job: '  Answer WhatsApp and book visits  ',
      idempotencyKey: 'k1',
      source: { provider: 'openai', model: 'gpt-x' },
    });
    expect(Object.keys(payload).every((key) => (HIRE_PAYLOAD_KEYS as readonly string[]).includes(key))).toBe(true);
    expect(payload).toMatchObject({
      idempotency_key: 'k1',
      job: 'Answer WhatsApp and book visits',
      name: 'Maya',
      role: 'Receptionist',
      apps: ['WhatsApp', 'Google Calendar'],
      rules: { ask_first: true, items: [{ key: 'hours', label: 'Only reply 9 to 6', value: false }] },
      choices: [{ key: 'report', label: 'Report', value: 'Daily' }],
      trigger: { kind: 'app_event', app: 'WhatsApp' },
      sends_via: 'WhatsApp',
      source: { spec_version: 1, provider: 'openai', model: 'gpt-x' },
    });
    expect(payload.steps).toEqual([
      { title: 'When a message arrives', detail: 'On WhatsApp', role: 'trigger', app: 'WhatsApp' },
      { title: 'Answer the question', detail: 'Using your notes', role: 'agent' },
      { title: 'Book the visit', detail: 'In your calendar', role: 'tool', app: 'Google Calendar' },
    ]);
    expect(fitsHireLimit(payload)).toBe(true);
  });

  it('follows the owner’s toggles and choices', () => {
    let state = setPath(spec.state, STATE_PATHS.askFirst, false);
    state = setPath(state, '/rules/hours', true);
    state = setPath(state, '/choices/report', 'Weekly');
    const payload = buildHirePayload({ spec, state, params: hireParams(spec, state), job: 'j', idempotencyKey: 'k' });
    expect(payload.rules.ask_first).toBe(false);
    expect(payload.rules.items).toEqual([{ key: 'hours', label: 'Only reply 9 to 6', value: true }]);
    expect(payload.choices[0].value).toBe('Weekly');
  });

  it('falls back to the agent card and a single step when the model left them out', () => {
    const bare = specOf('no hire or change button');
    const payload = buildHirePayload({ spec: bare, state: bare.state, params: {}, job: 'posts', idempotencyKey: 'k' });
    expect(payload.name).toBe('Sam');
    expect(payload.role).toBe('Social media helper');
    expect(payload.steps).toHaveLength(1);
    expect(payload.rules.ask_first).toBe(true);
    expect(payload.trigger).toBeUndefined();
  });

  it('refuses a malformed trigger and clamps long values', () => {
    const payload = buildHirePayload({
      spec,
      state: spec.state,
      params: { name: 'N'.repeat(80), trigger: { kind: 'sometimes', at: '25:99' } },
      job: 'j'.repeat(5000),
      idempotencyKey: 'k',
    });
    expect(payload.name).toHaveLength(40);
    expect(payload.job).toHaveLength(2000);
    expect(payload.trigger).toBeUndefined();
    const timed = buildHirePayload({
      spec,
      state: spec.state,
      params: { trigger: { kind: 'schedule', every: 'weekday', at: '09:30', app: 'Gmail' } },
      job: 'j',
      idempotencyKey: 'k',
    });
    expect(timed.trigger).toEqual({ kind: 'schedule', every: 'weekday', at: '09:30', app: 'Gmail' });
  });
});
