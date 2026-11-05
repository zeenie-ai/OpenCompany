/**
 * What a setup screen's buttons do, as json-render action handlers (given
 * to the screen's JSONUIProvider). Every Button presses one action through
 * its `on.press` binding; json-render resolves the binding's params against
 * the screen's state at the press and calls the handler with them. The
 * actions map onto Home: start editing the draft, open Connectors, connect
 * one app, or hire. `setState` is not here: json-render runs it itself,
 * writing the screen's state, which is the draft's own (HireScreen.tsx gives
 * json-render the draft through createStoreAdapter).
 *
 * `connect_app` names an app in the owner's words ("Gmail", "Google
 * calendar"). The server resolves the names a reply uses against the app
 * registry and sends them with it; a name it did not see is matched against
 * the connectable providers by name: exact, then prefix, then substring
 * (three characters or more). No match, or no name, opens Connectors.
 */

import type { AppRef } from '../data/schemas';
import type { ActionType } from './catalog';

export interface ConnectCandidate {
  providerId: string;
  name: string;
}

/** What the buttons can make Home do. */
export interface HireActions {
  /** Start editing the draft from the composer. */
  refine: () => void;
  openConnectors: () => void;
  /** Open the connect dialog for a provider. */
  connect: (providerId: string, appName: string) => void;
  hire: (params: Record<string, unknown>) => void;
}

export interface HireActionContext {
  /** Names the server resolved for this reply, lower-cased. */
  apps: Record<string, AppRef>;
  /** Connectable providers (the catalogue), for names the server did not see. */
  providers: readonly ConnectCandidate[];
  actions: HireActions;
}

export type HireActionHandlers = Record<Exclude<ActionType, 'setState'>, (params: Record<string, unknown>) => void>;

const MIN_SUBSTRING = 3;

/** The provider an app name refers to, by name: exact, then prefix (the
 *  longest match wins), then substring. */
export function matchProvider(query: string, candidates: readonly ConnectCandidate[]): ConnectCandidate | null {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  const named = candidates.map((candidate) => ({ candidate, name: candidate.name.trim().toLowerCase() }));
  const exact = named.find((entry) => entry.name === q);
  if (exact) return exact.candidate;
  const prefixed = named
    .filter((entry) => entry.name.startsWith(q) || q.startsWith(entry.name))
    .sort((a, b) => b.name.length - a.name.length);
  if (prefixed.length > 0) return prefixed[0].candidate;
  if (q.length < MIN_SUBSTRING) return null;
  const partial = named.find(
    (entry) => entry.name.includes(q) || (entry.name.length >= MIN_SUBSTRING && q.includes(entry.name)),
  );
  return partial?.candidate ?? null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function connectApp(params: Record<string, unknown>, context: HireActionContext): void {
  const appName = typeof params.app === 'string' ? params.app.trim() : '';
  if (!appName) {
    context.actions.openConnectors();
    return;
  }
  const known = Object.prototype.hasOwnProperty.call(context.apps, appName.toLowerCase())
    ? context.apps[appName.toLowerCase()]
    : undefined;
  if (known && known.supported) {
    context.actions.connect(known.provider_id, known.name);
    return;
  }
  const match = matchProvider(appName, context.providers);
  if (match) context.actions.connect(match.providerId, match.name);
  else context.actions.openConnectors();
}

/**
 * The handlers for a screen. `context` is read at each press, so the
 * handlers can be made once per screen (json-render's ActionProvider keeps
 * the handlers it first gets) while Home's callbacks and app lists change.
 */
export function hireActionHandlers(context: () => HireActionContext): HireActionHandlers {
  return {
    hire_employee: (params) => context().actions.hire(asRecord(params)),
    // A change is the owner's to write: whatever the button suggested is not sent.
    refine: () => context().actions.refine(),
    open_connectors: () => context().actions.openConnectors(),
    connect_app: (params) => connectApp(asRecord(params), context()),
  };
}
