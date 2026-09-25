/**
 * What a setup screen's buttons do. The model names an action; this maps it
 * onto Home: change a value, start editing the draft, open Connectors,
 * connect one app, or hire.
 *
 * `connect_app` names an app in the owner's words ("Gmail", "Google
 * calendar"). The server resolves the names a reply uses against the app
 * registry and sends them with it; a name it did not see is matched against
 * the connectable providers by name: exact, then prefix, then substring
 * (three characters or more). No match, or no name, opens Connectors.
 */

import type { AppRef } from '../data/schemas';
import { actionOf } from './catalog';
import { resolveValue, type UiState } from './expressions';

export interface ConnectCandidate {
  providerId: string;
  name: string;
}

export interface SpecActionHandlers {
  setValue: (path: string, value: unknown) => void;
  refine: () => void;
  openConnectors: () => void;
  /** Open the connect dialog for a provider. */
  connect: (providerId: string, appName: string) => void;
  hire: (params: Record<string, unknown>) => void;
}

export interface SpecActionContext {
  state: UiState;
  /** Names the server resolved for this reply, lower-cased. */
  apps: Record<string, AppRef>;
  /** Connectable providers (the catalogue), for names the server did not see. */
  providers: readonly ConnectCandidate[];
}

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

/** Run one button's action. `rawParams` are the button's params as
 *  written; they are resolved against the screen's current state here. */
export function runSpecAction(
  action: unknown,
  rawParams: unknown,
  context: SpecActionContext,
  handlers: SpecActionHandlers,
): void {
  const name = actionOf(action);
  const params = asRecord(resolveValue(rawParams ?? {}, context.state));
  switch (name) {
    case 'setState': {
      if (typeof params.statePath === 'string') handlers.setValue(params.statePath, params.value);
      return;
    }
    case 'refine':
      handlers.refine();
      return;
    case 'open_connectors':
      handlers.openConnectors();
      return;
    case 'connect_app': {
      const appName = typeof params.app === 'string' ? params.app.trim() : '';
      if (!appName) {
        handlers.openConnectors();
        return;
      }
      const known = context.apps[appName.toLowerCase()];
      if (known && known.supported) {
        handlers.connect(known.provider_id, known.name);
        return;
      }
      const match = matchProvider(appName, context.providers);
      if (match) handlers.connect(match.providerId, match.name);
      else handlers.openConnectors();
      return;
    }
    case 'hire_employee':
      handlers.hire(params);
      return;
    default:
      return;
  }
}
