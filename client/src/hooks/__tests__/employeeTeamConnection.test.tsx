import type { PropsWithChildren } from 'react';
import { act, renderHook } from '@testing-library/react';
import { ReactFlowProvider, type Edge, type Node } from 'reactflow';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useReactFlowNodes } from '../useReactFlowNodes';
import { useAppStore } from '../../store/useAppStore';
import * as nodeSpecs from '../../lib/nodeSpec';
import type { INodeTypeDescription } from '../../types/INodeProperties';

const node = (id: string, type: string): Node => ({ id, type, position: { x: 0, y: 0 }, data: {} });
const nodes = [node('lead', 'ai_employee'), node('talk', 'aiAgent'), node('worker', 'web_agent')];
const wrapper = ({ children }: PropsWithChildren) => <ReactFlowProvider>{children}</ReactFlowProvider>;

// What the backend specs declare: the lead carries leadsEmployeeTeam, and only
// the Talk agent has a Team input. The rule reads these, never the type names.
const specs: Record<string, Partial<INodeTypeDescription>> = {
  ai_employee: { uiHints: { leadsEmployeeTeam: true }, handles: [{ name: 'output-top', kind: 'output', position: 'top' }] },
  aiAgent: { uiHints: {}, handles: [{ name: 'output-top', kind: 'output', position: 'top' }, { name: 'input-employee-team', kind: 'input', position: 'bottom' }] },
  web_agent: { uiHints: {}, handles: [{ name: 'output-top', kind: 'output', position: 'top' }] },
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(nodeSpecs, 'resolveNodeDescription').mockImplementation(
    (type: string) => (specs[type] as INodeTypeDescription | undefined) ?? null,
  );
  useAppStore.setState({ currentWorkflow: { id: 'wf', nodes, edges: [] } as any });
});
afterEach(() => { vi.restoreAllMocks(); vi.clearAllTimers(); vi.useRealTimers(); });

it('connects the employee top output to the visible Talk Team input', () => {
  let edges: Edge[] = [];
  const { result } = renderHook(() => useReactFlowNodes({ setNodes: vi.fn(),
    setEdges: (update) => { edges = typeof update === 'function' ? update(edges) : update; },
  }), { wrapper });
  let accepted: boolean | undefined;
  act(() => { accepted = result.current.onConnect({ source: 'lead', target: 'talk', sourceHandle: 'output-top', targetHandle: 'input-employee-team' }); });
  expect(accepted).toBe(true);
  expect(edges).toHaveLength(1);
  expect(edges[0]).toMatchObject({ source: 'lead', target: 'talk', targetHandle: 'input-employee-team' });
});

it.each([
  { source: 'worker', target: 'talk', sourceHandle: 'output-top' },
  { source: 'lead', target: 'worker', sourceHandle: 'output-top' },
  { source: 'lead', target: 'talk', sourceHandle: 'output-main' },
])('rejects a non-team route before modifying the canvas: %j', (connection) => {
  const setEdges = vi.fn();
  const { result } = renderHook(() => useReactFlowNodes({ setNodes: vi.fn(), setEdges }), { wrapper });
  expect(result.current.onConnect({ ...connection, targetHandle: 'input-employee-team' })).toBe(false);
  expect(setEdges).not.toHaveBeenCalled();
});
