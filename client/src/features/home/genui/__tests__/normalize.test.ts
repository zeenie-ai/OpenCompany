/**
 * The normalizer's output is json-render's shape whichever shape the model
 * wrote: a Button's action becomes `on.press` (from `on.press`, from `on`
 * written inside its props, or from the older `action` / `actionParams`
 * props), a Toggle binds `checked` (older replies say `value`), and every
 * expression whose path reaches a prototype is dropped.
 *
 * The card it lays out (onboarding handoff R2): the agent card, the
 * routine, everything else as written, then the footer strip's ask-first
 * toggle and its row of the change button and the hire button, each named
 * in `layout` and kept however long the screen is.
 *
 * When they work, as the normalizer sets it up: the Plan's When row bound
 * to /trigger (no Schedule beside it), else one Schedule bound there.
 * /trigger starts as the hire button's trigger (else the app the routine's
 * first "When" step names, else the owner messaging them), snapped to a
 * trigger the server builds as it reads.
 */

import { describe, expect, it } from 'vitest';
import { STATE_PATHS } from '../catalog';
import { bindingPath, getPath } from '../expressions';
import { normalizeSpec, type NormalizedSpec } from '../normalize';

type Raw = Record<string, unknown>;

function screen(hireTrigger: unknown, extra: Record<string, Raw> = {}, planSteps: Raw[] = [{ title: 'Answer', role: 'agent' }]) {
  return normalizeSpec({
    root: 'r',
    elements: {
      r: { type: 'Stack', props: { direction: 'vertical' }, children: ['a', 'p', ...Object.keys(extra), 'row'] },
      a: { type: 'AgentCard', props: { name: 'Maya', role: 'Receptionist', apps: [] } },
      p: { type: 'Plan', props: { title: 'Their routine', steps: planSteps } },
      ...extra,
      row: { type: 'Stack', props: { direction: 'horizontal' }, children: ['h', 'c'] },
      h: { type: 'Button', props: { label: 'Hire Maya', variant: 'primary', action: 'hire_employee', actionParams: { trigger: hireTrigger } } },
      c: { type: 'Button', props: { label: 'Change something', action: 'refine' } },
    },
  })!;
}

/** A screen with just these elements under the root (plus whatever the normalizer adds). */
function only(elements: Record<string, Raw>, state: Raw = {}) {
  return normalizeSpec({
    root: 'r',
    state,
    elements: { r: { type: 'Stack', props: { direction: 'vertical' }, children: Object.keys(elements) }, ...elements },
  })!;
}

function schedules(spec: NormalizedSpec): string[] {
  return spec.order.filter((id) => spec.elements[id].type === 'Schedule');
}

function actionsOf(spec: NormalizedSpec): (string | undefined)[] {
  return spec.elements[spec.layout.actions].children.map((id) => spec.elements[id].on?.press.action);
}

describe('json-render shape', () => {
  const hireParams = { name: 'Maya', trigger: { kind: 'manual' } };

  it('takes a Button’s action from on.press, from on inside its props, or from its older props', () => {
    const shapes = [
      { type: 'Button', props: { label: 'Hire Maya' }, on: { press: { action: 'hire_employee', params: hireParams } } },
      { type: 'Button', props: { label: 'Hire Maya', on: { press: { action: 'hire_employee', params: hireParams } } } },
      { type: 'Button', props: { label: 'Hire Maya', action: 'hire_employee', actionParams: hireParams } },
      { type: 'Button', label: 'Hire Maya', action: 'hire_employee', actionParams: hireParams },
    ];
    for (const button of shapes) {
      const spec = only({ h: button });
      expect(spec.elements.h.on).toEqual({ press: { action: 'hire_employee', params: hireParams } });
      expect(spec.elements.h.props).toEqual({ label: 'Hire Maya', variant: 'primary' });
    }
  });

  it('prefers on.press over older props, and takes the first known action of a list', () => {
    const spec = only({
      h: {
        type: 'Button',
        props: { label: 'Hire', action: 'refine' },
        on: { press: [{ action: 'wave' }, { action: 'hire_employee', params: { name: 'Ivy' } }] },
      },
    });
    expect(spec.elements.h.on?.press).toEqual({ action: 'hire_employee', params: { name: 'Ivy' } });
  });

  it('keeps an action binding to its action and params', () => {
    const spec = only({
      h: {
        type: 'Button',
        props: { label: 'Hire' },
        on: {
          press: { action: 'hire_employee', params: { name: 'Lia' }, confirm: { title: 'Sure?', message: 'Hire?' }, onSuccess: { set: { '/x': 1 } } },
          hover: { action: 'refine' },
        },
      },
    });
    expect(spec.elements.h.on).toEqual({ press: { action: 'hire_employee', params: { name: 'Lia' } } });
  });

  it('turns ask into a plain change request and drops a Button whose action is unknown', () => {
    const spec = only({
      q: { type: 'Button', props: { label: 'Only weekdays' }, on: { press: { action: 'ask', params: { text: 'Only weekdays' } } } },
      x: { type: 'Button', props: { label: 'Push' }, on: { press: { action: 'pushState', params: { statePath: '/a', value: 1 } } } },
    });
    expect(spec.elements.q.on).toEqual({ press: { action: 'refine' } });
    expect(spec.elements.x).toBeUndefined();
  });

  it('binds a Toggle’s checked, reading value from older replies', () => {
    const spec = only({
      rules: { type: 'Card', props: { title: 'Ground rules' }, children: ['t1', 't2'] },
      t1: { type: 'Toggle', props: { label: 'Only 9 to 6', value: { $bindState: '/rules/hours' } } },
      t2: { type: 'Toggle', props: { label: 'Weekends', checked: { $bindState: '/rules/weekends' }, value: true } },
    });
    expect(spec.elements.t1.props).toEqual({ label: 'Only 9 to 6', checked: { $bindState: '/rules/hours' } });
    expect(spec.elements.t2.props).toEqual({ label: 'Weekends', checked: { $bindState: '/rules/weekends' } });
    // The ask-first toggle it adds binds checked too.
    const askFirst = spec.order.find((id) => bindingPath(spec.elements[id].props.checked) === STATE_PATHS.askFirst);
    expect(askFirst).toBeDefined();
  });

  it('drops expressions whose path reaches a prototype, wherever they are', () => {
    const spec = only({
      t: {
        type: 'Text',
        props: { text: { $template: 'Hi ${/__proto__/x} and ${ /name }' } },
        visible: { $state: '/constructor/x' },
      },
      m: { type: 'Metric', props: { label: { $state: '/prototype' }, value: { $computed: 'f' } }, visible: [{ $state: '/ok' }] },
      i: { type: 'Input', props: { label: 'Name', value: { $bindState: '/inputs/__proto__' } } },
      h: {
        type: 'Button',
        props: { label: 'Hire' },
        on: { press: { action: 'hire_employee', params: { name: { $state: '/__proto__/name' }, role: { $state: '/role' } } } },
      },
    });
    expect(spec.elements.t.props).toEqual({ text: { $template: 'Hi  and ${/name}' } });
    expect(spec.elements.t.visible).toBe(false);
    expect(spec.elements.m.props).toEqual({});
    expect(spec.elements.m.visible).toEqual({ $and: [{ $state: '/ok' }] });
    expect(spec.elements.i.props).toEqual({ label: 'Name' });
    expect(spec.elements.h.on?.press.params).toEqual({ role: { $state: '/role' } });
    expect(JSON.stringify(spec)).not.toMatch(/__proto__|constructor|prototype|\$computed/);
  });
});

describe('the card', () => {
  it('lays out who they are, the routine, the rest as written, then the footer', () => {
    const spec = screen({ kind: 'manual' }, { note: { type: 'Text', props: { text: 'Hello' } } });
    const { layout } = spec;
    expect(spec.elements.r.children).toEqual(['a', 'p', 'note', layout.askFirst, layout.actions]);
    expect(layout).toMatchObject({ identity: 'a', body: ['p', 'note'] });
    expect(bindingPath(spec.elements[layout.askFirst].props.checked)).toBe(STATE_PATHS.askFirst);
    expect(spec.elements[layout.actions]).toMatchObject({ type: 'Stack', props: { direction: 'horizontal' } });
    expect(actionsOf(spec)).toEqual(['refine', 'hire_employee']);
    // The model's own button row is left empty, so it goes.
    expect(spec.elements.row).toBeUndefined();
  });

  it('takes the agent card, the routine, the toggle and the buttons out of the containers they were in', () => {
    const spec = only({
      who: { type: 'Card', props: { title: 'Meet Maya' }, children: ['a', 'p'] },
      a: { type: 'AgentCard', props: { name: 'Maya', role: 'Receptionist' } },
      p: { type: 'Plan', props: { steps: [{ title: 'Answer', role: 'agent' }] } },
      rules: { type: 'Card', props: { title: 'Ground rules' }, children: ['ask', 'hours', 'h'] },
      ask: { type: 'Toggle', props: { label: 'Ask me first', checked: { $bindState: '/rules/askFirst' } } },
      hours: { type: 'Toggle', props: { label: 'Only 9 to 6', checked: { $bindState: '/rules/hours' } } },
      h: { type: 'Button', props: { label: 'Hire Maya' }, on: { press: { action: 'hire_employee' } } },
    });
    expect(spec.elements.r.children).toEqual(['a', 'p', 'rules', 'ask', spec.layout.actions]);
    expect(spec.elements.rules.children).toEqual(['hours']);
    // A card left with nothing in it goes.
    expect(spec.elements.who).toBeUndefined();
    expect(spec.layout.askFirst).toBe('ask');
  });

  it('keeps one agent card and one Plan, the first of each', () => {
    const spec = only({
      a: { type: 'AgentCard', props: { name: 'Maya', role: 'Receptionist' } },
      a2: { type: 'AgentCard', props: { name: 'Ivy', role: 'Diary keeper' } },
      p: { type: 'Plan', props: { steps: [{ title: 'Answer', role: 'agent' }] } },
      p2: { type: 'Plan', props: { steps: [{ title: 'Book', role: 'tool' }] } },
    });
    expect(spec.elements.a2).toBeUndefined();
    expect(spec.elements.p2).toBeUndefined();
    expect(spec.layout).toMatchObject({ identity: 'a', body: ['p'] });
  });

  it('makes the hire button primary and the change button secondary, whatever the model said', () => {
    const spec = only({
      h: { type: 'Button', props: { label: 'Hire Maya', variant: 'secondary' }, on: { press: { action: 'hire_employee' } } },
      c: { type: 'Button', props: { label: 'Change something', variant: 'primary' }, on: { press: { action: 'refine' } } },
    });
    expect(spec.elements.h.props.variant).toBe('primary');
    expect(spec.elements.c.props.variant).toBe('secondary');
  });

  it('adds the toggle and both buttons when the model left them out', () => {
    const spec = only({ a: { type: 'AgentCard', props: { name: 'Sam', role: 'Social media helper' } } });
    expect(spec.elements[spec.layout.askFirst].props.label).toBe('Ask me before sending anything');
    expect(getPath(spec.state, STATE_PATHS.askFirst)).toBe(true);
    expect(actionsOf(spec)).toEqual(['refine', 'hire_employee']);
    const [, hire] = spec.elements[spec.layout.actions].children;
    expect(spec.elements[hire].props).toEqual({ label: 'Hire Sam', variant: 'primary' });
  });

  it('has no identity without an agent card, and the routine comes first', () => {
    const spec = only({ note: { type: 'Text', props: { text: 'Hello' } } });
    const [schedule] = schedules(spec);
    expect(spec.layout).toMatchObject({ identity: null, body: [schedule, 'note'] });
  });
});

describe('when they work', () => {
  it('is the routine’s When row, bound to /trigger, with no Schedule beside it', () => {
    const spec = screen({ kind: 'app_event', app: 'WhatsApp' });
    expect(schedules(spec)).toHaveLength(0);
    expect(bindingPath(spec.elements.p.props.trigger)).toBe(STATE_PATHS.trigger);
    expect(getPath(spec.state, STATE_PATHS.trigger)).toEqual({ kind: 'app_event', app: 'WhatsApp' });
  });

  it('is one Schedule after the agent card when there is no routine', () => {
    const spec = only({
      a: { type: 'AgentCard', props: { name: 'Maya', role: 'Receptionist' } },
      note: { type: 'Text', props: { text: 'Hello' } },
    });
    const [schedule] = schedules(spec);
    expect(schedules(spec)).toHaveLength(1);
    expect(bindingPath(spec.elements[schedule].props.value)).toBe(STATE_PATHS.trigger);
    expect(spec.elements.r.children.slice(0, 3)).toEqual(['a', schedule, 'note']);
  });

  it('starts as the hire button’s trigger, snapped to what can run', () => {
    const spec = screen({ kind: 'schedule', every: 'weekday', at: '09:30', app: 'Gmail' });
    expect(getPath(spec.state, STATE_PATHS.trigger)).toEqual({ kind: 'schedule', every: 'weekday', at: '09:00' });
  });

  it('reads the hire button’s trigger from on.press as well', () => {
    const spec = only({
      a: { type: 'AgentCard', props: { name: 'Ivy', role: 'Diary keeper' } },
      h: {
        type: 'Button',
        props: { label: 'Hire Ivy', variant: 'primary' },
        on: { press: { action: 'hire_employee', params: { trigger: { kind: 'schedule', every: 'week', day: 'tue', at: '08:00' } } } },
      },
    });
    expect(getPath(spec.state, STATE_PATHS.trigger)).toEqual({ kind: 'schedule', every: 'week', day: 'tuesday', at: '08:00' });
  });

  it('takes the routine’s app when the button names none, else the owner messaging them', () => {
    const steps = [{ title: 'When a message arrives', role: 'trigger', app: 'Telegram' }, { title: 'Answer', role: 'agent' }];
    expect(getPath(screen(undefined, {}, steps).state, STATE_PATHS.trigger)).toEqual({ kind: 'app_event', app: 'Telegram' });
    expect(getPath(screen({ kind: 'app_event' }, {}, steps).state, STATE_PATHS.trigger)).toEqual({
      kind: 'app_event',
      app: 'Telegram',
    });
    expect(getPath(screen(undefined).state, STATE_PATHS.trigger)).toEqual({ kind: 'manual' });
    expect(getPath(screen({ kind: 'manual' }, {}, steps).state, STATE_PATHS.trigger)).toEqual({ kind: 'manual' });
  });

  it('keeps one Schedule the model wrote when there is no routine, rebound, and drops the rest', () => {
    const spec = only({ s1: { type: 'Schedule', props: { value: 'whenever' } }, s2: { type: 'Schedule', props: {} } });
    expect(schedules(spec)).toEqual(['s1']);
    expect(bindingPath(spec.elements.s1.props.value)).toBe(STATE_PATHS.trigger);
  });

  it('drops every Schedule the model wrote beside a routine', () => {
    const spec = screen({ kind: 'manual' }, { s1: { type: 'Schedule', props: { value: 'whenever' } } });
    expect(schedules(spec)).toEqual([]);
    expect(bindingPath(spec.elements.p.props.trigger)).toBe(STATE_PATHS.trigger);
  });

  it('survives the size cap, with the rest of the card', () => {
    const texts = Object.fromEntries(
      Array.from({ length: 30 }, (_, i) => [`t${i}`, { type: 'Text', props: { text: `Line ${i}` } }]),
    );
    const spec = screen({ kind: 'manual' }, texts);
    expect(spec.order.length).toBeLessThanOrEqual(16);
    expect(spec.elements.p).toBeDefined();
    expect(spec.layout.identity).toBe('a');
    expect(spec.elements[spec.layout.askFirst]).toBeDefined();
    expect(actionsOf(spec)).toEqual(['refine', 'hire_employee']);
  });
});
