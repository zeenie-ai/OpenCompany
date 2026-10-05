import { StrictMode, type ReactNode } from 'react';
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAppStore, type WorkflowData } from '../../store/useAppStore';
import { useWorkflowCanvas } from '../useWorkflowCanvas';

function workflow(id: string, label = id): WorkflowData {
  return {
    id, name: label, slug: label, createdAt: new Date(0), lastModified: new Date(0),
    nodes: [
      { id: `${id}:agent`, type: 'aiAgent', position: { x: 100, y: 200 }, data: { label } },
      { id: `${id}:reply`, type: 'chatReply', position: { x: 400, y: 200 }, data: {} },
    ],
    edges: [{ id: `${id}:edge`, source: `${id}:agent`, target: `${id}:reply` }],
  };
}

const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;
const mount = () => renderHook(() => useWorkflowCanvas(100), { wrapper });

beforeEach(() => {
  vi.useFakeTimers();
  useAppStore.setState({ currentWorkflow: workflow('a'), hasUnsavedChanges: false, selectedNode: null, workflowUIStates: {} });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('employee canvas ownership', () => {
  it('keeps the preloaded graph through StrictMode mount, debounce, and cleanup', () => {
    const original = useAppStore.getState().currentWorkflow;
    const { result, unmount } = mount();
    expect(result.current.nodes).toEqual(original!.nodes);
    expect(result.current.edges).toEqual(original!.edges);
    act(() => vi.advanceTimersByTime(1_000));
    unmount();
    expect(useAppStore.getState().currentWorkflow).toBe(original);
    expect(useAppStore.getState().hasUnsavedChanges).toBe(false);
  });

  it('does not count React Flow measuring the nodes as an edit', () => {
    // On mount React Flow gives each node its measured size; none of that is
    // saved, so opening a workflow must not mark it changed and save it.
    const original = useAppStore.getState().currentWorkflow;
    const { result } = mount();
    act(() => result.current.setNodes(nodes => nodes.map(node => ({ ...node, width: 120, height: 80, positionAbsolute: node.position }))));
    act(() => vi.advanceTimersByTime(1_000));
    expect(useAppStore.getState().currentWorkflow).toBe(original);
    expect(useAppStore.getState().hasUnsavedChanges).toBe(false);
  });

  it('flushes edits made just before leaving Dev and preserves them on returning', () => {
    const first = mount();
    act(() => first.result.current.setNodes(nodes => nodes.map(node => ({ ...node, position: { x: 900, y: 100 } }))));
    first.unmount();
    expect(useAppStore.getState().hasUnsavedChanges).toBe(true);
    const second = mount();
    expect(second.result.current.nodes[0].position).toEqual({ x: 900, y: 100 });
    act(() => vi.advanceTimersByTime(100));
    expect(useAppStore.getState().currentWorkflow!.nodes[0].position).toEqual({ x: 900, y: 100 });
  });

  it('shows the next employee without flushing the previous canvas into it', () => {
    const { result } = mount();
    act(() => result.current.setNodes(nodes => nodes.slice(0, 1)));
    const next = workflow('b');
    act(() => useAppStore.setState({ currentWorkflow: next, hasUnsavedChanges: false }));
    expect(result.current.nodes).toEqual(next.nodes);
    expect(result.current.edges).toEqual(next.edges);
    act(() => vi.advanceTimersByTime(1_000));
    expect(useAppStore.getState().currentWorkflow).toBe(next);
    expect(useAppStore.getState().hasUnsavedChanges).toBe(false);
  });

  it.each(['a', 'b'])('does not overwrite a newer %s snapshot if it arrives immediately before unmount', (id) => {
    const { result, unmount } = mount();
    act(() => result.current.setNodes(nodes => nodes.slice(0, 1)));
    const incoming = workflow(id, 'new saved graph');
    act(() => {
      useAppStore.setState({ currentWorkflow: incoming, hasUnsavedChanges: false });
      unmount();
    });
    expect(useAppStore.getState().currentWorkflow).toBe(incoming);
    expect(useAppStore.getState().hasUnsavedChanges).toBe(false);
  });

  it('adopts a same-ID reload even when its modification timestamp is unchanged', () => {
    const { result } = mount();
    const incoming = workflow('a', 'updated');
    act(() => useAppStore.setState({ currentWorkflow: incoming }));
    expect(result.current.nodes[0].data.label).toBe('updated');
    act(() => vi.advanceTimersByTime(100));
    expect(useAppStore.getState().hasUnsavedChanges).toBe(false);
  });

  it('persists an intentional deletion of every node', () => {
    const { result } = mount();
    act(() => {
      result.current.setNodes([]);
      result.current.setEdges([]);
    });
    act(() => vi.advanceTimersByTime(100));
    expect(useAppStore.getState().currentWorkflow).toMatchObject({ nodes: [], edges: [] });
    expect(useAppStore.getState().hasUnsavedChanges).toBe(true);
  });

  it('does not mark a node selection as a graph edit', () => {
    const { result } = mount();
    act(() => result.current.onNodesChange([{ id: 'a:agent', type: 'select', selected: true }]));
    act(() => vi.advanceTimersByTime(100));
    expect(useAppStore.getState().hasUnsavedChanges).toBe(false);
  });
});
