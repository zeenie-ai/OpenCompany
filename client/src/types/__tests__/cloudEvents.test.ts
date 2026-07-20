/**
 * Parity tests for the FE `WorkflowEvent` interface + `matchesType` helper
 * vs the backend `WorkflowEvent.matches_type` Pydantic method.
 *
 * Mirrors the cases in `server/tests/services/test_events.py::test_matches_type_glob`
 * exactly so a future divergence (in the server-side regex or the FE
 * regex) fails one or both suites.
 */

import { describe, it, expect } from 'vitest';
import {
  cloudEventIdentity,
  isAgentCapabilityEvent,
  matchesType,
  type WorkflowEvent,
} from '../cloudEvents';

const sample: WorkflowEvent = {
  specversion: '1.0',
  id: 'abc123',
  source: 'stripe://x',
  type: 'stripe.charge.succeeded',
  time: '2026-05-06T12:00:00Z',
  data: {},
};

describe('matchesType', () => {
  it('matches the wildcard "all"', () => {
    expect(matchesType(sample, 'all')).toBe(true);
  });

  it('matches the empty string as wildcard', () => {
    expect(matchesType(sample, '')).toBe(true);
  });

  it('matches an exact type', () => {
    expect(matchesType(sample, 'stripe.charge.succeeded')).toBe(true);
  });

  it('matches a leaf-prefix glob', () => {
    expect(matchesType(sample, 'stripe.charge.*')).toBe(true);
  });

  it('matches a root-prefix glob', () => {
    expect(matchesType(sample, 'stripe.*')).toBe(true);
  });

  it('returns false for a non-matching prefix glob', () => {
    expect(matchesType(sample, 'payment_intent.*')).toBe(false);
  });

  it('returns false for a non-matching exact type', () => {
    expect(matchesType(sample, 'stripe.charge.failed')).toBe(false);
  });

  it('treats the prefix-only form as exact (no trailing dot)', () => {
    // Server-side parity: matches_type('stripe') only matches type='stripe',
    // NOT 'stripe.charge.succeeded' — the glob form requires '.*' to span.
    expect(matchesType(sample, 'stripe')).toBe(false);
    const root: WorkflowEvent = { ...sample, type: 'stripe' };
    expect(matchesType(root, 'stripe')).toBe(true);
    expect(matchesType(root, 'stripe.*')).toBe(true);
  });
});

describe('WorkflowEvent shape', () => {
  it('parses a backend-emitted credential_catalogue_updated payload', () => {
    // Sample shape from server/services/status_broadcaster.py's
    // broadcast_credential_event — copy of what hits the WS today.
    const payload = {
      type: 'credential_catalogue_updated',
      data: {
        specversion: '1.0',
        id: 'a1b2c3d4',
        source: 'opencompany://services/credentials',
        type: 'com.opencompany.credential.api_key.saved',
        time: '2026-05-06T12:34:56.789Z',
        subject: 'openai',
        datacontenttype: 'application/json',
        dataschema: 'opencompany://schemas/events/credential.api_key.saved.json',
        data: { provider: 'openai' },
      },
    };
    const envelope = payload.data as WorkflowEvent<{ provider: string }>;
    expect(envelope.specversion).toBe('1.0');
    expect(envelope.type).toBe('com.opencompany.credential.api_key.saved');
    expect(envelope.subject).toBe('openai');
    expect(envelope.data.provider).toBe('openai');
    // matchesType strips the com.opencompany. prefix, so callers write
    // patterns without it.
    expect(matchesType(envelope, 'credential.api_key.*')).toBe(true);
    expect(matchesType(envelope, 'credential.*')).toBe(true);
    expect(matchesType(envelope, 'stripe.*')).toBe(false);
  });

  it('matches replayed events that use the pre-rebrand namespace', () => {
    const legacyEnvelope: WorkflowEvent = {
      ...sample,
      source: 'machinaos://services/credentials',
      type: 'com.machinaos.credential.api_key.saved',
    };

    expect(matchesType(legacyEnvelope, 'credential.api_key.*')).toBe(true);
    expect(matchesType(legacyEnvelope, 'credential.*')).toBe(true);
  });

  it('preserves legacy extension-shaped compatibility fields when present', () => {
    const event: WorkflowEvent = {
      ...sample,
      workflow_id: 'wf_42',
      trigger_node_id: 'trigger_abc',
      correlation_id: 'corr_xyz',
    };
    expect(event.workflow_id).toBe('wf_42');
    expect(event.trigger_node_id).toBe('trigger_abc');
    expect(event.correlation_id).toBe('corr_xyz');
  });
});

describe('agent capability CloudEvents', () => {
  const capability = {
    specversion: '1.0',
    id: 'cap-1',
    source: 'opencompany://services/agent',
    type: 'com.opencompany.agent.skill.loading',
    time: '2026-07-20T12:00:00Z',
    subject: 'agent-1',
    data: {
      workflow_id: '7',
      execution_id: '3',
      agent_node_id: 'agent-1',
      author_node_id: 'agent-1',
      target_node_id: 'master-1',
      capability_kind: 'skill',
      capability_name: 'todo-skill',
      state: 'loading',
    },
  };

  it('accepts the exact subject/type/state contract', () => {
    expect(isAgentCapabilityEvent(capability)).toBe(true);
    expect(cloudEventIdentity(capability)).toBe('opencompany://services/agent\u0000cap-1');
  });

  it('rejects spoofed subjects and invalid kind/state pairs', () => {
    expect(isAgentCapabilityEvent({ ...capability, subject: 'agent-2' })).toBe(false);
    expect(isAgentCapabilityEvent({
      ...capability,
      type: 'com.opencompany.agent.skill.started',
      data: { ...capability.data, state: 'started' },
    })).toBe(false);
  });

  it('rejects a mismatched or non-canonical source', () => {
    expect(isAgentCapabilityEvent({
      ...capability,
      source: 'opencompany://services/other',
    })).toBe(false);
  });
});
