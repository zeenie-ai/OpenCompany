/**
 * "Answering": one of an employee's nodes is executing right now, the
 * watched ones or the agent that answers the owner in Talk. It decides
 * Working versus Ready (data/presentation) and the hire view's count.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { isAnswering, useAnsweringCount, workingNodeIds } from '../data/liveTask';
import { parseEmployee, type EmployeeSummary } from '../data/schemas';

function employee(id: string, patch: Record<string, unknown> = {}): EmployeeSummary {
  return parseEmployee({
    workflow_id: id,
    name: id,
    status: 'working',
    watch_node_ids: [`${id}:aiAgent:1`],
    talk: { state: 'on', agent_node_id: `${id}:talk` },
    control: normalizeWorkflowControlStatus({ state: 'running' }, id),
    ...patch,
  })!;
}

afterEach(() => {
  useNodeStatusStore.setState({ allStatuses: {} });
});

describe('workingNodeIds', () => {
  it('adds the talk agent to the watched nodes once', () => {
    expect(workingNodeIds(employee('a'))).toEqual(['a:aiAgent:1', 'a:talk']);
    expect(workingNodeIds(employee('a', { watch_node_ids: ['a:talk'] }))).toEqual(['a:talk']);
    expect(workingNodeIds(employee('a', { talk: { state: 'off', agent_node_id: null } }))).toEqual(['a:aiAgent:1']);
  });
});

describe('isAnswering', () => {
  it('is true only while one of the nodes executes', () => {
    const ids = ['a:aiAgent:1', 'a:talk'];
    expect(isAnswering(undefined, ids)).toBe(false);
    expect(isAnswering({ 'a:talk': { status: 'executing' } }, ids)).toBe(true);
    expect(isAnswering({ 'a:aiAgent:1': { status: 'success' } }, ids)).toBe(false);
    // Another node of theirs that is not watched does not count.
    expect(isAnswering({ 'a:other:1': { status: 'executing' } }, ids)).toBe(false);
  });
});

describe('useAnsweringCount', () => {
  it('counts the running employees who are answering, in their own workflow only', () => {
    const team = [employee('a'), employee('b'), employee('c', { status: 'ready' })];
    const { result } = renderHook(() => useAnsweringCount(team));
    expect(result.current).toBe(0);
    act(() => {
      useNodeStatusStore.setState({
        allStatuses: {
          a: { 'a:talk': { status: 'executing' } },
          // b's id under another workflow is someone else's node.
          c: { 'b:aiAgent:1': { status: 'executing' }, 'c:aiAgent:1': { status: 'executing' } },
        },
      });
    });
    // c is not running, so its executing node does not make it count.
    expect(result.current).toBe(1);
  });
});
