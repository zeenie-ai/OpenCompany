import { describe, expect, it } from 'vitest';

import { todoQueryKey, todoQueryKeyFromEvent } from '../todoQuery';


describe('todoQueryKey', () => {
  it('isolates sibling writeTodos nodes in one workflow', () => {
    expect(todoQueryKey('workflow-1', 'node-a')).toEqual([
      'todos',
      'v2',
      'workflow-1',
      'node-a',
    ]);
    expect(todoQueryKey('workflow-1', 'node-a')).not.toEqual(
      todoQueryKey('workflow-1', 'node-b'),
    );
  });

  it('uses the explicit unsaved scope without losing node identity', () => {
    expect(todoQueryKey(undefined, 'node-a')).toEqual([
      'todos',
      'v2',
      'unsaved',
      'node-a',
    ]);
  });
});


describe('todoQueryKeyFromEvent', () => {
  it('routes a modern event to the exact workflow and node cache', () => {
    expect(
      todoQueryKeyFromEvent({
        session_key: 'todo:v2:workflow-1:node-b',
        workflow_id: 'workflow-1',
        node_id: 'node-b',
      }),
    ).toEqual(todoQueryKey('workflow-1', 'node-b'));
  });

  it('routes an unsaved node event to the exact unsaved cache', () => {
    expect(
      todoQueryKeyFromEvent({
        session_key: 'todo:v2:unsaved:node-b',
        node_id: 'node-b',
      }),
    ).toEqual(['todos', 'v2', 'unsaved', 'node-b']);
  });

  it('preserves the legacy session-key route when node_id is missing', () => {
    expect(
      todoQueryKeyFromEvent({ session_key: 'legacy-workflow' }),
    ).toEqual(['todos', 'legacy-workflow']);
    expect(todoQueryKeyFromEvent({}, 'legacy-subject')).toEqual([
      'todos',
      'legacy-subject',
    ]);
  });

  it('does not route an event without any identity', () => {
    expect(todoQueryKeyFromEvent({})).toBeNull();
  });
});
