import { describe, expect, it } from 'vitest';
import * as core from '@json-render/core';
import { writePath } from '@/lib/jsonRender/paths';
import corpus from '../__fixtures__/replies.json';
import { STARTERS } from '../../hire/templates';
import { STATE_PATHS } from '../catalog';
import {
  HIRE_PAYLOAD_KEYS,
  SCHEDULE_TIMES,
  buildHirePayload,
  fitsHireLimit,
  nearestTime,
  routineSteps,
  snapTrigger,
  starterHirePayload,
  triggerSentence,
} from '../hirePayload';
import { normalizeSpec, type NormalizedSpec } from '../normalize';
import { parseReply } from '../parse';

const replies = corpus as unknown as { name: string; reply: string }[];

function specOf(name: string): NormalizedSpec {
  const parsed = parseReply(replies.find((c) => c.name === name)!.reply);
  return normalizeSpec(parsed.spec, core)!;
}

/** The hire button's params as json-render resolves them at the press. */
function hireParams(spec: NormalizedSpec, state = spec.state) {
  const id = spec.order.find((key) => spec.elements[key].on?.press.action === 'hire_employee')!;
  return core.resolvePropValue(spec.elements[id].on?.press.params ?? {}, { stateModel: state }) as Record<string, unknown>;
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
      core,
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

  it('hires the card the server draws while Generated UI is off', () => {
    const drawn = specOf('fixed form the server draws while Generated UI is off');
    const payload = buildHirePayload({ spec: drawn, state: drawn.state, params: hireParams(drawn), job: 'Answer WhatsApp', idempotencyKey: 'k2', core });
    expect(payload).toMatchObject({
      name: 'Maya',
      role: 'Receptionist',
      description: 'Answers WhatsApp and books visits.',
      apps: ['WhatsApp', 'Google Calendar'],
      rules: { ask_first: true, items: [{ key: 'rule1', label: 'Only reply 9 to 6', value: false }] },
      choices: [],
      inputs: [],
      trigger: { kind: 'app_event', app: 'WhatsApp' },
      sends_via: 'WhatsApp',
    });
    expect(payload.steps).toEqual([
      { title: 'When a message arrives', detail: 'On WhatsApp', role: 'trigger', app: 'WhatsApp' },
      { title: 'Answer the question', detail: 'Using your notes', role: 'agent' },
      { title: 'Book the visit', detail: 'In your calendar', role: 'tool', app: 'Google Calendar' },
    ]);
  });

  it('follows the owner’s toggles and choices', () => {
    let state = writePath(spec.state, STATE_PATHS.askFirst, false);
    state = writePath(state, '/rules/hours', true);
    state = writePath(state, '/choices/report', 'Weekly');
    const payload = buildHirePayload({ spec, state, params: hireParams(spec, state), job: 'j', idempotencyKey: 'k', core });
    expect(payload.rules.ask_first).toBe(false);
    expect(payload.rules.items).toEqual([{ key: 'hours', label: 'Only reply 9 to 6', value: true }]);
    expect(payload.choices[0].value).toBe('Weekly');
  });

  it('reads a reply in json-render’s shape the same way', () => {
    const shaped = specOf('json-render shape: on.press and checked');
    const state = writePath(shaped.state, '/rules/hours', true);
    const payload = buildHirePayload({ spec: shaped, state, params: hireParams(shaped, state), job: 'j', idempotencyKey: 'k', core });
    expect(payload).toMatchObject({
      name: 'Maya',
      rules: { ask_first: true, items: [{ key: 'hours', label: 'Only reply 9 to 6', value: true }] },
      choices: [{ key: 'report', label: 'Report', value: 'Daily' }],
      trigger: { kind: 'app_event', app: 'WhatsApp' },
      sends_via: 'WhatsApp',
    });
  });

  it('sends when they work as the owner left it, and the routine says so', () => {
    const state = writePath(spec.state, STATE_PATHS.trigger, { kind: 'schedule', every: 'weekday', at: '09:00' });
    const payload = buildHirePayload({ spec, state, params: hireParams(spec, state), job: 'j', idempotencyKey: 'k', core });
    // The screen's answer wins over the hire button's own trigger.
    expect(payload.trigger).toEqual({ kind: 'schedule', every: 'weekday', at: '09:00' });
    expect(payload.steps[0]).toEqual({ title: 'Every weekday at 09:00', detail: '', role: 'trigger' });
    expect(payload.steps.slice(1).map((step) => step.title)).toEqual(['Answer the question', 'Book the visit']);
  });

  it('falls back to the agent card and a single step when the model left them out', () => {
    const bare = specOf('no hire or change button');
    const payload = buildHirePayload({ spec: bare, state: bare.state, params: {}, job: 'posts', idempotencyKey: 'k', core });
    expect(payload.name).toBe('Sam');
    expect(payload.role).toBe('Social media helper');
    expect(payload.steps).toHaveLength(1);
    expect(payload.rules.ask_first).toBe(true);
    // Nothing said what starts the work: they work when the owner messages them.
    expect(payload.trigger).toEqual({ kind: 'manual' });
  });

  it('reads the hire button’s trigger when the screen has none, snapped, and clamps long values', () => {
    const state = writePath(spec.state, STATE_PATHS.trigger, undefined);
    const payload = buildHirePayload({
      spec,
      state,
      params: { name: 'N'.repeat(80), trigger: { kind: 'sometimes', at: '25:99' } },
      job: 'j'.repeat(5000),
      idempotencyKey: 'k',
      core,
    });
    expect(payload.name).toHaveLength(40);
    expect(payload.job).toHaveLength(2000);
    expect(payload.trigger).toBeUndefined();
    const timed = buildHirePayload({
      spec,
      state,
      params: { trigger: { kind: 'schedule', every: 'weekday', at: '09:30', app: 'Gmail' } },
      job: 'j',
      idempotencyKey: 'k',
      core,
    });
    expect(timed.trigger).toEqual({ kind: 'schedule', every: 'weekday', at: '09:00' });
  });

  /** A screen with one rule toggle in the rules card, written as `toggle`. */
  function ruleScreen(toggle: Record<string, unknown>) {
    return normalizeSpec(
      {
        root: 'r',
        state: { rules: { hours: true }, n: 5 },
        elements: {
          r: { type: 'Stack', props: { direction: 'vertical' }, children: ['a', 'g', 'h'] },
          a: { type: 'AgentCard', props: { name: 'Maya', role: 'Receptionist', apps: [] }, children: [] },
          g: { type: 'Card', props: { title: 'Ground rules' }, children: ['t'] },
          t: { type: 'Toggle', children: [], ...toggle },
          h: {
            type: 'Button',
            props: { label: 'Hire Maya', variant: 'primary' },
            on: { press: { action: 'hire_employee', params: { name: 'Maya', role: 'Receptionist' } } },
            children: [],
          },
        },
      },
      core,
    )!;
  }

  it('reads a control’s expressions the way the screen does', () => {
    const screen = ruleScreen({
      props: {
        label: { $cond: { $and: [{ $state: '/rules/hours' }, { $state: '/n', gt: 3 }] }, $then: 'Busy hours', $else: 'Quiet hours' },
        checked: { $bindState: '/rules/hours' },
      },
    });
    const payload = buildHirePayload({ spec: screen, state: screen.state, params: hireParams(screen), job: 'j', idempotencyKey: 'k', core });
    expect(payload.rules.items).toEqual([{ key: 'hours', label: 'Busy hours', value: true }]);
  });

  it('sends only the controls the screen shows', () => {
    const screen = ruleScreen({
      props: { label: 'Only reply 9 to 6', checked: { $bindState: '/rules/hours' } },
      visible: { $state: '/n', lt: 3 },
    });
    const payload = buildHirePayload({ spec: screen, state: screen.state, params: hireParams(screen), job: 'j', idempotencyKey: 'k', core });
    expect(payload.rules.items).toEqual([]);
  });
});

describe('when they work', () => {
  it('snaps a time to one the schedule runs at, the earlier of two equally close', () => {
    expect(nearestTime('09:00')).toBe('09:00');
    expect(nearestTime('09:30')).toBe('09:00');
    expect(nearestTime('11:00')).toBe('10:00');
    expect(nearestTime('13:10')).toBe('14:00');
    expect(nearestTime('23:30')).toBe('00:00');
    expect(nearestTime('8:15')).toBe('08:00');
    expect(nearestTime('8am')).toBe('09:00');
    expect(SCHEDULE_TIMES).toContain(nearestTime('17:00'));
  });

  it('keeps only what a trigger of that kind can use', () => {
    expect(snapTrigger(undefined)).toEqual({ kind: 'manual' });
    expect(snapTrigger({ kind: 'manual', app: 'WhatsApp', at: '09:00' })).toEqual({ kind: 'manual' });
    expect(snapTrigger({ kind: 'app_event', app: '  Gmail ', every: 'day' })).toEqual({ kind: 'app_event', app: 'Gmail' });
    expect(snapTrigger({ kind: 'schedule' })).toEqual({ kind: 'schedule', every: 'day', at: '09:00' });
    expect(snapTrigger({ kind: 'schedule', every: 'hour', at: '10:00' })).toEqual({ kind: 'schedule', every: 'hour' });
    expect(snapTrigger({ kind: 'schedule', every: 'week', day: 'Thu', at: '07:00' })).toEqual({
      kind: 'schedule',
      every: 'week',
      day: 'thursday',
      at: '06:00',
    });
    expect(snapTrigger({ kind: 'schedule', every: 'month', day: 31 })).toEqual({ kind: 'schedule', every: 'month', day: '1', at: '09:00' });
    expect(snapTrigger({ kind: 'schedule', every: 'month', day: '15' }).day).toBe('15');
  });

  it('says it in the words the employee’s card uses', () => {
    expect(triggerSentence({ kind: 'manual' })).toBe('When you message them');
    expect(triggerSentence({ kind: 'app_event', app: 'whatsapp' }, 'WhatsApp')).toBe('When something new arrives in WhatsApp');
    expect(triggerSentence({ kind: 'schedule', every: 'hour' })).toBe('Every hour');
    expect(triggerSentence({ kind: 'schedule', every: 'weekday', at: '09:00' })).toBe('Every weekday at 09:00');
    expect(triggerSentence({ kind: 'schedule', every: 'week', day: 'monday', at: '08:00' })).toBe('Every Monday at 08:00');
    expect(triggerSentence({ kind: 'schedule', every: 'month', day: '1', at: '10:00' })).toBe('On day 1 of every month at 10:00');
    expect(triggerSentence({ kind: 'schedule', every: 'day', at: '20:00' })).toBe('Every day at 20:00');
  });

  it('rewrites the routine’s first step only once the trigger changed', () => {
    const steps = [
      { title: 'When a message arrives', detail: 'On WhatsApp', role: 'trigger' as const, app: 'WhatsApp' },
      { title: 'Answer', role: 'agent' as const },
    ];
    const written = { kind: 'app_event' as const, app: 'WhatsApp' };
    expect(routineSteps(steps, written, { kind: 'app_event', app: 'whatsapp' })).toEqual(steps);
    expect(routineSteps(steps, written, { kind: 'manual' })[0]).toEqual({ title: 'When you message them', role: 'trigger' });
    expect(routineSteps(steps, written, { kind: 'app_event', app: 'Gmail' })[0]).toEqual({
      title: 'When something new arrives in Gmail',
      role: 'trigger',
      app: 'Gmail',
    });
  });
});

describe('starterHirePayload', () => {
  const receptionist = STARTERS.find((starter) => starter.id === 'receptionist')!;

  it('hires a starter as it stands, asking first', () => {
    const payload = starterHirePayload(receptionist, { idempotencyKey: 'k', taken: [] });
    expect(Object.keys(payload).every((key) => (HIRE_PAYLOAD_KEYS as readonly string[]).includes(key))).toBe(true);
    expect(payload).toMatchObject({
      idempotency_key: 'k',
      job: receptionist.job,
      name: receptionist.hire.names[0],
      role: receptionist.hire.role,
      apps: receptionist.apps,
      rules: { ask_first: true, items: [] },
      trigger: receptionist.hire.trigger,
      sends_via: 'WhatsApp',
    });
    expect(payload.steps[0]).toMatchObject({ role: 'trigger', app: 'WhatsApp' });
    expect(fitsHireLimit(payload)).toBe(true);
  });

  it('never needs its trigger snapped', () => {
    for (const starter of STARTERS) {
      expect(starterHirePayload(starter, { idempotencyKey: 'k', taken: [] }).trigger).toEqual(starter.hire.trigger);
    }
  });

  it('takes a name nobody on the team has', () => {
    const [first, second] = receptionist.hire.names;
    expect(starterHirePayload(receptionist, { idempotencyKey: 'k', taken: [first.toUpperCase()] }).name).toBe(second);
    const everyName = [...receptionist.hire.names, `${first} 2`];
    expect(starterHirePayload(receptionist, { idempotencyKey: 'k', taken: everyName }).name).toBe(`${first} 3`);
  });
});
