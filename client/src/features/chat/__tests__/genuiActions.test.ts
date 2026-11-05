/**
 * A reply's interface made ready to draw (genui/prepare.ts) and what its
 * buttons do (genui/actions.ts): unknown components are left out, each
 * button's press is tagged with its element and label, the state the owner
 * last set is read as plain data, `ask` sends only what the button says, and
 * any other action goes back to the employee.
 */

import { describe, expect, it, vi } from 'vitest';
import booking from '../__fixtures__/saturday-booking.spec.json';
import { asksWhatItSays, chatActionHandlers, type ChatUiActions } from '../genui/actions';
import { PRESS_ELEMENT, PRESS_LABEL, prepareChatSpec, prepareChatState, untagged } from '../genui/prepare';

describe('prepareChatSpec', () => {
  it('keeps the handoff example and tags each press with its element and label', () => {
    const spec = prepareChatSpec(booking);
    expect(spec?.root).toBe('root');
    expect(Object.keys(spec?.elements ?? {})).toEqual(Object.keys(booking.elements));
    expect(spec?.elements.hold.on?.press).toEqual({
      action: 'holdSlot',
      params: { slot: { $state: '/slot' }, [PRESS_ELEMENT]: 'hold', [PRESS_LABEL]: 'Hold this slot for 15 min' },
    });
  });

  it('leaves out components the chat does not have, and children naming them', () => {
    const spec = prepareChatSpec({
      root: 'root',
      state: {},
      elements: {
        root: { type: 'Stack', props: {}, children: ['t', 'x', 'later'] },
        t: { type: 'Text', props: { text: 'hi' } },
        x: { type: 'Marquee', props: {} },
      },
    });
    expect(Object.keys(spec?.elements ?? {})).toEqual(['root', 't']);
    // A child not here yet may still be on its way.
    expect(spec?.elements.root.children).toEqual(['t', 'later']);
  });

  it('draws nothing until the root has arrived', () => {
    expect(prepareChatSpec({ root: '', state: {}, elements: {} })).toBeNull();
    expect(prepareChatSpec({ root: 'root', state: {}, elements: {} })).toBeNull();
    expect(prepareChatSpec('nope')).toBeNull();
  });

  it('reads a press back without its tags', () => {
    expect(untagged({ slot: 's2', [PRESS_ELEMENT]: 'hold', [PRESS_LABEL]: 'Hold' })).toEqual({
      elementId: 'hold',
      label: 'Hold',
      params: { slot: 's2' },
    });
  });
});

describe('prepareChatState', () => {
  it('reads what the owner last set as plain data, without prototype keys', () => {
    const saved = JSON.parse('{"service":"Highlights","__proto__":{"admin":true},"form":{"constructor":{"x":1},"name":"Ana"}}');
    const state = prepareChatState(saved);
    expect(state).toEqual({ service: 'Highlights', form: { name: 'Ana' } });
    expect(Object.prototype.hasOwnProperty.call(state, '__proto__')).toBe(false);
  });

  it('reads anything but an object as no state', () => {
    expect(prepareChatState(null)).toEqual({});
    expect(prepareChatState(['Highlights'])).toEqual({});
  });
});

describe('chatActionHandlers', () => {
  function handlers() {
    const actions: ChatUiActions = { ask: vi.fn(), event: vi.fn() };
    return { actions, handlers: chatActionHandlers('ui_1', () => actions) };
  }

  it('sends ask with its text and the label to compare it with', () => {
    const { actions, handlers: h } = handlers();
    h.ask({ text: ' What does Sunday look like? ', [PRESS_ELEMENT]: 'sun', [PRESS_LABEL]: 'Show Sunday instead' });
    expect(actions.ask).toHaveBeenCalledWith('What does Sunday look like?', 'Show Sunday instead');
    h.ask({ text: '' });
    expect(actions.ask).toHaveBeenCalledTimes(1);
  });

  it('sends any other action back to the employee, naming the press', () => {
    const { actions, handlers: h } = handlers();
    h.holdSlot({ slot: 's2', [PRESS_ELEMENT]: 'hold', [PRESS_LABEL]: 'Hold this slot for 15 min' });
    expect(actions.event).toHaveBeenCalledWith({
      partId: 'ui_1',
      elementId: 'hold',
      action: 'holdSlot',
      params: { slot: 's2' },
      label: 'Hold this slot for 15 min',
    });
  });

  it('answers no name that is not an action', () => {
    const { handlers: h } = handlers();
    for (const name of ['then', 'toJSON', 'toString', 'constructor', '__proto__', '9lives']) {
      expect(h[name], name).toBeUndefined();
    }
  });
});

describe('asksWhatItSays', () => {
  it('compares the text with the label, ignoring case and spacing', () => {
    expect(asksWhatItSays('Show Sunday', ' show  sunday ')).toBe(true);
    expect(asksWhatItSays('What does Sunday look like?', 'Show Sunday instead')).toBe(false);
    expect(asksWhatItSays('', '')).toBe(false);
  });
});
