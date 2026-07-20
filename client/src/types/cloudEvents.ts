/**
 * CloudEvents v1.0 envelope — mirrors the backend Pydantic `WorkflowEvent`
 * (`server/services/events/envelope.py`) so the FE can statically type
 * payloads broadcast over the WebSocket without falling back to `any`.
 *
 * Spec: https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md
 *
 * Wire shape on the WebSocket today (preserved end-to-end across the
 * PartySocket / TanStack-Query swap):
 *
 *     {
 *       "type": "credential_catalogue_updated",  // legacy outer routing key
 *       "data": {
 *         "specversion": "1.0",
 *         "id": "<uuid>",
 *         "source": "opencompany://services/credentials",
 *         "type": "com.opencompany.credential.api_key.saved",  // reverse-DNS per Primer
 *         "time": "2026-05-06T12:34:56.789Z",
 *         "subject": "openai",
 *         "datacontenttype": "application/json",
 *         "dataschema": "opencompany://schemas/events/credential.api_key.saved.json",
 *         "data": { "provider": "openai", ... }
 *       }
 *     }
 *
 * Future Wave 12 sources (`stripe.*`, `telegram.*`, `task.*`) will use the
 * same envelope. The `matchesType` helper provides server-parity glob
 * dispatch (mirrors `WorkflowEvent.matches_type` in envelope.py) — patterns
 * match against the type with the `com.opencompany.` prefix stripped.
 *
 * Pytest invariant `server/tests/test_credential_broadcasts.py` locks the
 * backend shape; the FE vitest counterpart locks this interface.
 */

export interface WorkflowEvent<TData = unknown> {
  // CloudEvents v1.0 required context attributes.
  specversion: '1.0';
  id: string;
  source: string;
  type: string;
  /**
   * CloudEvents makes `time` optional; OpenCompany's producer model always
   * supplies it. Pydantic JSON mode serialises it as an RFC 3339 timestamp.
   */
  time: string;
  subject?: string;
  datacontenttype?: string;
  dataschema?: string;
  data: TData;

  // Legacy OpenCompany extension attributes. Their underscores are not
  // CloudEvents-conformant; new event contracts keep workflow/execution
  // scope in `data` instead. These remain optional compatibility reads.
  // `model_config = ConfigDict(extra="allow")` on the Pydantic side means
  // additional fields may appear and should be tolerated; cast through
  // `WorkflowEvent<T> & {extra?: unknown}` if you need to read them.
  workflow_id?: string | null;
  trigger_node_id?: string | null;
  correlation_id?: string | null;
}

export type AgentCapabilityKind = 'skill' | 'tool';
export type AgentCapabilityState =
  | 'loading'
  | 'loaded'
  | 'resource_read'
  | 'cleared'
  | 'started'
  | 'completed'
  | 'failed';

/** Safe payload for `com.opencompany.agent.(skill|tool).*` events. */
export interface AgentCapabilityLifecycleData {
  workflow_id: string;
  execution_id?: string;
  root_execution_id?: string;
  agent_node_id: string;
  author_node_id: string;
  target_node_id?: string;
  capability_kind: AgentCapabilityKind;
  capability_name: string;
  state: AgentCapabilityState;
  action?: string;
  provider?: string;
  invocation_source?: string;
  tool_call_id?: string;
  duration_ms?: number;
  returned_characters?: number;
  token_estimate?: number;
  content_hash?: string;
  error_code?: string;
}

const AGENT_CAPABILITY_STATES: Record<AgentCapabilityKind, ReadonlySet<AgentCapabilityState>> = {
  skill: new Set(['loading', 'loaded', 'resource_read', 'failed', 'cleared']),
  tool: new Set(['started', 'completed', 'failed']),
};

/** Strict consumer guard for the agent-capability CloudEvents contract. */
export function isAgentCapabilityEvent(
  value: unknown,
): value is WorkflowEvent<AgentCapabilityLifecycleData> {
  if (!value || typeof value !== 'object') return false;
  const event = value as Partial<WorkflowEvent<Partial<AgentCapabilityLifecycleData>>>;
  const payload = event.data;
  if (!payload || typeof payload !== 'object') return false;
  const kind = payload.capability_kind;
  const state = payload.state;
  if (kind !== 'skill' && kind !== 'tool') return false;
  if (typeof state !== 'string' || !AGENT_CAPABILITY_STATES[kind].has(state as AgentCapabilityState)) {
    return false;
  }
  return (
    event.specversion === '1.0' &&
    typeof event.id === 'string' && event.id.length > 0 &&
    event.source === 'opencompany://services/agent' &&
    event.type === `com.opencompany.agent.${kind}.${state}` &&
    typeof payload.workflow_id === 'string' && payload.workflow_id.length > 0 &&
    typeof payload.agent_node_id === 'string' && payload.agent_node_id.length > 0 &&
    typeof payload.author_node_id === 'string' &&
    payload.author_node_id === payload.agent_node_id &&
    event.subject === payload.agent_node_id &&
    typeof payload.capability_name === 'string' && payload.capability_name.length > 0
  );
}

/** CloudEvents duplicate identity is the tuple `(source, id)`. */
export function cloudEventIdentity(event: Pick<WorkflowEvent, 'source' | 'id'>): string {
  return `${event.source}\u0000${event.id}`;
}

const TYPE_PREFIXES = [
  'com.opencompany.',
  // Accept events persisted or replayed from before the product rename.
  'com.machinaos.',
] as const;

/**
 * Glob-style match on the CloudEvents `type` field.
 *
 * Mirrors `WorkflowEvent.matches_type` in `server/services/events/envelope.py`.
 * Patterns are matched against the type with the `com.opencompany.` reverse-DNS
 * prefix stripped, so callers can write `'credential.api_key.*'` and still
 * hit `com.opencompany.credential.api_key.saved`. The pre-rebrand namespace
 * remains accepted for replay compatibility. External-producer types
 * (e.g. `stripe.charge.succeeded` from a Stripe webhook) have no prefix
 * and match directly.
 *
 * Test parity is locked by `client/src/types/__tests__/cloudEvents.test.ts`
 * vs the corresponding pytest cases.
 *
 *   matchesType(e, 'stripe.charge.succeeded')  // exact (external)
 *   matchesType(e, 'credential.api_key.*')     // prefix glob (internal)
 *   matchesType(e, 'agent.*')                  // prefix glob (1 segment)
 *   matchesType(e, 'all')                      // wildcard
 *   matchesType(e, '')                         // wildcard
 */
export function matchesType(event: WorkflowEvent, pattern: string): boolean {
  if (!pattern || pattern === 'all') return true;
  const raw = event.type ?? '';
  const matchedPrefix = TYPE_PREFIXES.find((prefix) => raw.startsWith(prefix));
  const normalized = matchedPrefix ? raw.slice(matchedPrefix.length) : raw;
  if (pattern.endsWith('.*')) {
    const prefix = pattern.slice(0, -2);
    return normalized === prefix || normalized.startsWith(prefix + '.');
  }
  return normalized === pattern;
}
