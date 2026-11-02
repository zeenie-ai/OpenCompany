/**
 * What a reply's buttons do, as json-render action handlers. json-render
 * runs `setState` itself; every other action reaches these with its params
 * resolved against the UI's state at the press (and the press's tags,
 * genui/prepare.ts):
 *
 * - `ask` with the text the button would send. The chat sends it as the
 *   owner's message only when it is exactly what the button says; otherwise
 *   the text goes into the message box for the owner to read first
 *   (`ChatUiActions.ask` decides).
 * - Any other name goes back to the employee as a button press
 *   (`ChatUiActions.event`), the UI, element, action and params named.
 *
 * Action names are open-ended (the employee names its own), so the handlers
 * are a Proxy that answers any usable name; json-render keeps the object it
 * is given and looks names up on it.
 */

import { isUsableName } from '@/lib/jsonRender/sanitize';
import { untagged } from './prepare';

export interface ChatUiEvent {
  partId: string;
  elementId: string;
  action: string;
  params: Record<string, unknown>;
  /** The pressed button's label, as it read at the press. */
  label: string;
}

export interface ChatUiActions {
  ask: (text: string, label: string) => void;
  event: (event: ChatUiEvent) => void;
}

/** Names no handler object should answer: a Proxy that answered `then`
 *  would look like a promise. (json-render runs its own actions, setState
 *  and the rest, before it asks the handlers.) */
const NOT_HANDLED = new Set(['then', 'toJSON']);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The handlers for one UI. `actions` is read at each press, so the
 *  handlers can be made once per UI while the chat's callbacks change. */
export function chatActionHandlers(
  partId: string,
  actions: () => ChatUiActions,
): Record<string, (params: Record<string, unknown>) => void> {
  const handle = (action: string) => (raw: Record<string, unknown>) => {
    const press = untagged(isRecord(raw) ? raw : {});
    if (action === 'ask') {
      const text = typeof press.params.text === 'string' ? press.params.text.trim() : '';
      if (text) actions().ask(text, press.label);
      return;
    }
    actions().event({ partId, elementId: press.elementId, action, params: press.params, label: press.label });
  };
  return new Proxy(Object.create(null) as Record<string, (params: Record<string, unknown>) => void>, {
    get(_target, name) {
      if (typeof name !== 'string' || NOT_HANDLED.has(name) || !isUsableName(name)) return undefined;
      if (name in Object.prototype) return undefined;
      return handle(name);
    },
  });
}

/** Whether `ask`'s text is what its button says, so it can go at once. */
export function asksWhatItSays(text: string, label: string): boolean {
  const norm = (value: string) => value.replace(/\s+/g, ' ').trim().toLowerCase();
  return norm(text).length > 0 && norm(text) === norm(label);
}
