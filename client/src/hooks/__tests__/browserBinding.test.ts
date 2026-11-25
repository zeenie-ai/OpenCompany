import { beforeEach, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import type { Node } from 'reactflow';
const { sendRequest, getNodeParameters } = vi.hoisted(() => ({ sendRequest: vi.fn(), getNodeParameters: vi.fn() }));
vi.mock('../../contexts/WebSocketContext', () => ({ useWebSocket: () => ({ sendRequest, getNodeParameters, saveNodeParameters: vi.fn() }) }));
vi.mock('../useUserSettingsQuery', () => ({ useUserSettingsQuery: () => ({ data: { auto_add_skill_for_tools: true } }) }));
import { browserBindingProblem, useAutoSkillEdges } from '../useAutoSkillEdges';
import { useAppStore } from '../../store/useAppStore';
import { queryClient } from '../../lib/queryClient';
import { nodeParamsQueryKey } from '../useNodeParamsQuery';
import { nodeSpecQueryKey } from '../../lib/nodeSpec';
const agent = { id: 'agent', type: 'browser_agent', data: {}, position: { x: 0, y: 0 } } as Node;
const browser = { id: 'browser', type: 'browser', data: {}, position: { x: 0, y: 0 } } as Node;
beforeEach(() => {
  sendRequest.mockReset();
  getNodeParameters.mockReset();
  queryClient.clear();
  queryClient.setQueryData(nodeSpecQueryKey('browser_agent'), { uiHints: { createsBrowserAgent: true } });
});
it('allows legacy connections and only the exact reserved browser for a fresh agent', () => {
  expect(browserBindingProblem(browser, { ...agent, data: {} }, 'input-tools')).toBeNull();
  expect(browserBindingProblem(browser, agent, 'input-tools', 1)).toBe('private_browser_required');
  const reserved = { ...browser, data: { private_browser_owner: { agent_node_id: 'agent', runtime_version: 1 } } };
  expect(browserBindingProblem(reserved, agent, 'input-tools', 1)).toBeNull();
  expect(browserBindingProblem(reserved, { ...agent, data: {} }, 'input-tools')).toBe('reserved_browser');
  expect(browserBindingProblem(reserved, { ...agent, id: 'other' }, 'input-tools', 1)).toBe('private_browser_required');
});

it('checks authoritative saved version before connection or automatic skill side effects', async () => {
  const graph = { id: 'wf', nodes: [browser, agent], edges: [] };
  useAppStore.setState({ currentWorkflow: graph as any });
  // Caller-supplied graph data must not turn a fresh saved agent into legacy.
  queryClient.setQueryData(nodeParamsQueryKey('agent'), { parameters: { runtime_behavior_version: 1 } });
  const baseOnConnect = vi.fn();
  const { result } = renderHook(() => useAutoSkillEdges({ baseOnConnect, baseOnEdgesDelete: vi.fn(), nodes: graph.nodes, edges: [], setNodes: vi.fn(), setEdges: vi.fn() }));
  await act(async () => result.current.onConnect({ source: 'browser', target: 'agent', targetHandle: 'input-tools', sourceHandle: null }));
  expect(baseOnConnect).not.toHaveBeenCalled();
  expect(sendRequest).not.toHaveBeenCalled();
  expect(getNodeParameters).not.toHaveBeenCalled();
});

it.each(['coding_agent', 'aiAgent', 'orchestrator_agent'])('rejects a Browser tool on %s before offering a private profile or adding an edge', async (type) => {
  const graph = { id: 'wf', nodes: [browser, { ...agent, type }], edges: [] };
  useAppStore.setState({ currentWorkflow: graph as any });
  queryClient.setQueryData(nodeSpecQueryKey(type), { uiHints: {} });
  const baseOnConnect = vi.fn();
  const { result } = renderHook(() => useAutoSkillEdges({ baseOnConnect, baseOnEdgesDelete: vi.fn(), nodes: graph.nodes, edges: [], setNodes: vi.fn(), setEdges: vi.fn() }));
  await act(async () => { await result.current.onConnect({ source: 'browser', target: 'agent', targetHandle: 'input-tools', sourceHandle: 'output-tool' }); });
  expect(baseOnConnect).not.toHaveBeenCalled();
  expect(getNodeParameters).not.toHaveBeenCalled();
  expect(sendRequest).not.toHaveBeenCalled();
});

it('ignores a pending saved-version read after its graph has changed', async () => {
  const graph = { id: 'wf', nodes: [browser, agent], edges: [] };
  useAppStore.setState({ currentWorkflow: graph as any });
  let resolve!: (value: unknown) => void;
  getNodeParameters.mockImplementation(() => new Promise((done) => { resolve = done; }));
  const baseOnConnect = vi.fn();
  const { result } = renderHook(() => useAutoSkillEdges({ baseOnConnect, baseOnEdgesDelete: vi.fn(), nodes: graph.nodes, edges: [], setNodes: vi.fn(), setEdges: vi.fn() }));
  await act(async () => {
    const first = result.current.onConnect({ source: 'browser', target: 'agent', targetHandle: 'input-tools', sourceHandle: null });
    const duplicate = result.current.onConnect({ source: 'browser', target: 'agent', targetHandle: 'input-tools', sourceHandle: null });
    useAppStore.setState({ currentWorkflow: { ...graph } as any });
    resolve({ parameters: {} });
    await Promise.all([first, duplicate]);
  });
  expect(getNodeParameters).toHaveBeenCalledOnce();
  expect(baseOnConnect).not.toHaveBeenCalled();
  expect(sendRequest).not.toHaveBeenCalled();
});

it('does not dispatch duplicate Browser auto-skill work before the accepted edge renders', async () => {
  const graph = { id: 'wf', nodes: [browser, agent], edges: [] };
  useAppStore.setState({ currentWorkflow: graph as any });
  queryClient.setQueryData(nodeParamsQueryKey('agent'), { parameters: {} });
  const baseOnConnect = vi.fn(() => true);
  const { result } = renderHook(() => useAutoSkillEdges({ baseOnConnect, baseOnEdgesDelete: vi.fn(), nodes: graph.nodes, edges: graph.edges, setNodes: vi.fn(), setEdges: vi.fn() }));
  const connection = { source: 'browser', target: 'agent', targetHandle: 'input-tools', sourceHandle: null };
  await act(async () => { await result.current.onConnect(connection); await result.current.onConnect(connection); });
  expect(baseOnConnect).toHaveBeenCalledOnce();
  expect(sendRequest).toHaveBeenCalledOnce();
});

it('ignores a pending read after local canvas edits even before the store debounce', async () => {
  const graph = { id: 'wf', nodes: [browser, agent], edges: [] };
  useAppStore.setState({ currentWorkflow: graph as any });
  let resolve!: (value: unknown) => void;
  getNodeParameters.mockImplementation(() => new Promise((done) => { resolve = done; }));
  const baseOnConnect = vi.fn();
  const { result, rerender } = renderHook(({ nodes }) => useAutoSkillEdges({ baseOnConnect, baseOnEdgesDelete: vi.fn(), nodes, edges: graph.edges, setNodes: vi.fn(), setEdges: vi.fn() }), { initialProps: { nodes: graph.nodes } });
  let pending!: Promise<void>;
  act(() => { pending = result.current.onConnect({ source: 'browser', target: 'agent', targetHandle: 'input-tools', sourceHandle: null }); });
  rerender({ nodes: [agent] });
  await act(async () => { resolve({ parameters: {} }); await pending; });
  expect(baseOnConnect).not.toHaveBeenCalled();
  expect(sendRequest).not.toHaveBeenCalled();
});
