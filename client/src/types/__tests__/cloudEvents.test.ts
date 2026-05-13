/**
 * Parity tests for the FE `WorkflowEvent` interface + `matchesType` helper
 * vs the backend `WorkflowEvent.matches_type` Pydantic method.
 *
 * Mirrors the cases in `server/tests/services/test_events.py::test_matches_type_glob`
 * exactly so a future divergence (in the server-side regex or the FE
 * regex) fails one or both suites.
 */

import { describe, it, expect } from 'vitest';
import { matchesType, type WorkflowEvent } from '../cloudEvents';

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
        source: 'machinaos://services/credentials',
        type: 'com.machinaos.credential.api_key.saved',
        time: '2026-05-06T12:34:56.789Z',
        subject: 'openai',
        datacontenttype: 'application/json',
        dataschema: 'machinaos://schemas/events/credential.api_key.saved.json',
        data: { provider: 'openai' },
      },
    };
    const envelope = payload.data as WorkflowEvent<{ provider: string }>;
    expect(envelope.specversion).toBe('1.0');
    expect(envelope.type).toBe('com.machinaos.credential.api_key.saved');
    expect(envelope.subject).toBe('openai');
    expect(envelope.data.provider).toBe('openai');
    // matchesType strips the com.machinaos. prefix, so callers write
    // patterns without it.
    expect(matchesType(envelope, 'credential.api_key.*')).toBe(true);
    expect(matchesType(envelope, 'credential.*')).toBe(true);
    expect(matchesType(envelope, 'stripe.*')).toBe(false);
  });

  it('preserves CloudEvents extension attributes when present', () => {
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
