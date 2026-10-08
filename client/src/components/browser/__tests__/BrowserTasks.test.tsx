import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { createAgent, adopt } = vi.hoisted(() => ({ createAgent: vi.fn(), adopt: vi.fn() }));
vi.mock('@/services/browserAgentApi', () => ({ createBrowserAgent: createAgent }));
vi.mock('@/store/useAppStore', () => ({ useAppStore: { getState: () => ({ adoptSavedOperations: adopt }) } }));
import BrowserTasks from '../BrowserTasks';

const task = { invocation_id: 'inv', submission_id: 'sub', node_id: 'agent', prompt: 'Read my account', status: 'running' };
const response = (body: unknown, ok = true) => ({ ok, json: async () => body });
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  createAgent.mockReset(); adopt.mockReset();
  fetchMock = vi.fn(async (url: string) => response(url.includes('discovery') ? { agents: [{ node_id: 'agent', label: 'Browser AI Agent' }] } : { items: [task], next_cursor: null }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe('direct Browser tasks', () => {
  it('submits through Workspace admission and shows isolated recent history', async () => {
    render(<BrowserTasks workflowId="wf" browserNodeId="browser" visible />);
    fireEvent.change(await screen.findByLabelText('Browser task'), { target: { value: 'Read my account' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(JSON.parse(init.body)).toMatchObject({ workflow_id: 'wf', agent_node_id: 'agent', prompt: 'Read my account' });
    expect(screen.getByText('Read my account')).toBeInTheDocument();
    expect(screen.getByLabelText('Browser task')).toHaveValue('');
  });
  it('reuses the submission ID when a response is lost', async () => {
    const submitted: string[] = [];
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') { submitted.push(JSON.parse(init.body as string).submission_id); if (submitted.length === 1) throw new Error('Connection lost'); return response({ status: 'accepted' }); }
      return response(url.includes('discovery') ? { agents: [{ node_id: 'agent', label: 'Agent' }] } : { items: [], next_cursor: null });
    });
    render(<BrowserTasks workflowId="wf" browserNodeId="browser" visible />);
    fireEvent.change(await screen.findByLabelText('Browser task'), { target: { value: 'Read an issue' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: 'Run task' }));
    await waitFor(() => expect(submitted).toHaveLength(2));
    expect(submitted[0]).toBe(submitted[1]);
  });
  it('cancels the admitted task by its server identity', async () => {
    render(<BrowserTasks workflowId="wf" browserNodeId="browser" visible />);
    await screen.findByLabelText('Browser task');
    fireEvent.click(screen.getByRole('button', { name: /Recent tasks/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel task' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => url.includes('/tasks/sub?') && init?.method === 'DELETE')).toBe(true));
  });
  it('adds the agent through the shared saved-graph recipe', async () => {
    fetchMock.mockResolvedValue(response({ agents: [] }));
    createAgent.mockResolvedValue({ node_ids: { agent: 'new' }, operations: ['saved-operation'] });
    render(<BrowserTasks workflowId="wf" browserNodeId="browser" visible />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add Browser AI Agent' }));
    await waitFor(() => expect(createAgent).toHaveBeenCalledWith({ workflow_id: 'wf', browser_node_id: 'browser' }));
    expect(adopt).toHaveBeenCalledWith('wf', ['saved-operation']);
  });
});
