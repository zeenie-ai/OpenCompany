import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ isReady: false, sendRequest: vi.fn(), addEventListener: () => () => {} }),
}));
vi.mock('@/services/workflowApi', () => ({
  workflowApi: { deleteWorkflow: vi.fn(), saveWorkflow: vi.fn(), getWorkflow: vi.fn(), getAllWorkflows: vi.fn() },
}));
vi.mock('@/components/brand/Logo', () => ({ OcLogo: () => null }));
vi.mock('../data/profile', () => ({ useOwnerSettings: () => ({ data: { profile_full_name: 'Owner' } }) }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { queryClient } from '@/lib/queryClient';
import { workflowApi } from '@/services/workflowApi';
import { useAppStore } from '@/store/useAppStore';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { EMPLOYEES_QUERY_KEY } from '../data/employeeCache';
import { parseEmployee } from '../data/schemas';
import { HomeSidebar } from '../sidebar/HomeSidebar';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

function mountSidebar() {
  return render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <HomeSidebar />
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

function confirmDelete() {
  fireEvent.click(screen.getByRole('button', { name: 'Delete Maya' }));
  const dialog = screen.getByRole('alertdialog', { name: 'Delete Maya?' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
  return dialog;
}

beforeEach(() => {
  queryClient.clear();
  vi.mocked(workflowApi.deleteWorkflow).mockReset().mockResolvedValue(true);
  vi.mocked(workflowApi.saveWorkflow).mockReset();
  vi.mocked(pillToast).mockClear();
  useAppStore.setState({ currentWorkflow: null, hasUnsavedChanges: false, selectedNode: null, workflowUIStates: {} });
  useHomeStore.setState({
    sidebarOpen: true,
    view: { kind: 'employee', workflowId: 'b' },
    workspaceFor: 'b',
    hireNotice: null,
    glow: null,
  });
  queryClient.setQueryData(EMPLOYEES_QUERY_KEY, [
    parseEmployee({ workflow_id: 'a', name: 'Maya', role: 'Receptionist' })!,
    parseEmployee({ workflow_id: 'b', name: 'Theo', role: 'Researcher' })!,
  ]);
});

afterEach(() => {
  queryClient.clear();
  vi.restoreAllMocks();
});

describe('HomeSidebar employee deletion', () => {
  it('offers a separate delete button and cancellation never selects or deletes the employee', () => {
    mountSidebar();
    const button = screen.getByRole('button', { name: 'Delete Maya' });
    expect(button.parentElement?.closest('button')).toBeNull();
    fireEvent.click(button);
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'b' });
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cancel' }));
    expect(workflowApi.deleteWorkflow).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Delete Maya' })).toBeInTheDocument();
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'b' });
  });

  it('deletes the selected employee through the store and returns to hiring without creating a replacement', async () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'a' }, workspaceFor: 'a' });
    mountSidebar();
    confirmDelete();
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Delete Maya' })).not.toBeInTheDocument());
    expect(workflowApi.deleteWorkflow).toHaveBeenCalledWith('a');
    expect(workflowApi.saveWorkflow).not.toHaveBeenCalled();
    expect(useHomeStore.getState().view).toEqual({ kind: 'hire' });
    expect(useHomeStore.getState().workspaceFor).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete Theo' })).toBeInTheDocument();
  });

  it('disables confirmation while pending and sends only one delete', async () => {
    let finish!: (success: boolean) => void;
    vi.mocked(workflowApi.deleteWorkflow).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    mountSidebar();
    const dialog = confirmDelete();
    const pendingButton = within(dialog).getByRole('button', { name: 'Deleting…' });
    expect(pendingButton).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled();
    fireEvent.click(pendingButton);
    expect(workflowApi.deleteWorkflow).toHaveBeenCalledTimes(1);
    await act(async () => { finish(true); });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'b' });
  });

  it('keeps the employee and selection on failure, shows an error, and allows retry', async () => {
    vi.mocked(workflowApi.deleteWorkflow).mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    vi.spyOn(console, 'error').mockImplementation(() => {});
    mountSidebar();
    confirmDelete();
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Couldn’t delete Maya. Try again.', { tone: 'error' }));
    expect(useHomeStore.getState().view).toEqual({ kind: 'employee', workflowId: 'b' });
    expect(queryClient.getQueryData<Array<{ workflow_id: string }>>(EMPLOYEES_QUERY_KEY)?.map((employee) => employee.workflow_id)).toEqual(['a', 'b']);
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(workflowApi.deleteWorkflow).toHaveBeenCalledTimes(2);
  });
});

describe('HomeSidebar status pip', () => {
  afterEach(() => {
    useNodeStatusStore.setState({ allStatuses: {} });
  });

  it('pulses only while a running employee is answering', () => {
    queryClient.setQueryData(EMPLOYEES_QUERY_KEY, [
      parseEmployee({
        workflow_id: 'a',
        name: 'Maya',
        role: 'Receptionist',
        status: 'working',
        watch_node_ids: ['a:aiAgent:1'],
        control: normalizeWorkflowControlStatus({ state: 'running' }, 'a'),
      })!,
    ]);
    const { container } = mountSidebar();
    expect(container.querySelector('[data-pip="on"]')).toBeNull();
    act(() => {
      useNodeStatusStore.setState({ allStatuses: { a: { 'a:aiAgent:1': { status: 'executing' } } } });
    });
    expect(container.querySelector('[data-pip="on"]')).not.toBeNull();
    act(() => {
      useNodeStatusStore.setState({ allStatuses: { a: { 'a:aiAgent:1': { status: 'success' } } } });
    });
    expect(container.querySelector('[data-pip="on"]')).toBeNull();
  });
});
