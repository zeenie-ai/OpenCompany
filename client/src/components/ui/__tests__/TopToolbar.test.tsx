import type { ComponentProps } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { WorkflowControlStatus } from '../../../contexts/WebSocketContext';

const mocks = vi.hoisted(() => ({
  sendRequest: vi.fn(),
  storedSignature: '',
}));

// Keep the real useApiKeys response boundary and lifecycle helpers: the
// regression was an asynchronous credential response crashing the toolbar.
vi.mock('../../../contexts/WebSocketContext', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../contexts/WebSocketContext')>(),
  useWebSocket: () => ({ isConnected: true, sendRequest: mocks.sendRequest }),
}));
vi.mock('../../../hooks/useCatalogueQuery', () => ({
  useStoredCredentialSignature: () => mocks.storedSignature,
}));
vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, logout: vi.fn() }),
}));
vi.mock('../ThemeButton', () => ({ ThemeButton: () => null }));
vi.mock('../../shell/ModeToggle', () => ({ ModeToggle: () => null }));

import TopToolbar from '../TopToolbar';
import { useCanvasDockStore } from '../../../stores/canvasDockStore';

const running: WorkflowControlStatus = {
  workflow_id: 'workflow-1',
  revision: 2,
  state: 'running',
  active_count: 1,
  in_flight_count: 1,
  queued_count: 0,
  can_start: false,
  can_pause: true,
  can_resume: false,
  can_reset: true,
  can_edit: false,
};

function toolbarProps(): ComponentProps<typeof TopToolbar> {
  return {
    workflowName: 'Browser workflow',
    onWorkflowNameChange: vi.fn(),
    onSave: vi.fn(),
    onNew: vi.fn(),
    onOpen: vi.fn(),
    onRun: vi.fn(),
    workflowControl: running,
    onStartWorkflow: vi.fn(),
    onPauseWorkflow: vi.fn(),
    onResumeWorkflow: vi.fn(),
    onResetWorkflow: vi.fn(),
    hasUnsavedChanges: false,
    sidebarVisible: true,
    onToggleSidebar: vi.fn(),
    componentPaletteVisible: true,
    onToggleComponentPalette: vi.fn(),
    proMode: true,
    onToggleProMode: vi.fn(),
    onOpenSettings: vi.fn(),
    onOpenCredentials: vi.fn(),
    onExportJSON: vi.fn(),
    onExportFile: vi.fn(),
    onImportJSON: vi.fn(),
    onGlobalModelChange: vi.fn(),
  };
}

// The model menu (cmdk) scrolls its highlighted row into view; jsdom has no scrolling.
beforeAll(() => {
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  mocks.sendRequest.mockReset();
  mocks.storedSignature = '';
  useCanvasDockStore.setState({ open: false });
});

// Colour marks state or role (docs-internal/theme_system.md): the toolbar's
// controls are neutral, Start is its one invert primary, and only the save
// dot carries a colour.
describe('TopToolbar colour', () => {
  // An action, node-role or status colour among an element's classes. Every
  // Button carries `aria-invalid:border-destructive`, which is no colour of
  // its own.
  const COLOUR = /(^|:)(bg|text|border)-(action|node|success|warning|destructive|info)(-|$)/;
  const coloured = (container: HTMLElement) =>
    [...container.querySelectorAll('*')].filter((element) =>
      (element.getAttribute('class') ?? '').split(/\s+/).some((name) => !name.includes('aria-invalid:') && COLOUR.test(name)),
    );
  const withGlobalModel = { providers: [{ provider: 'openai', models: ['gpt-4.1'] }], global_provider: 'openai', global_model: 'gpt-4.1' };
  const ready = { ...running, state: 'ready' as const, can_start: true, can_pause: false, can_reset: false, can_edit: true };

  it('draws Start as the invert primary, with no colour but the save dot', async () => {
    mocks.sendRequest.mockResolvedValue(withGlobalModel);
    const { container } = render(<TopToolbar {...toolbarProps()} workflowControl={ready} hasUnsavedChanges />);
    expect(await screen.findByRole('button', { name: 'Apply All' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Start' })).toHaveAttribute('data-variant', 'invert');
    const label = screen.getByText('Modified');
    expect(label).toHaveClass('text-fg-muted');
    expect(coloured(container)).toEqual([label.querySelector('div')]);
    expect(label.querySelector('div')).toHaveClass('bg-warning');
  });

  it('draws Resume as the invert primary too', async () => {
    mocks.sendRequest.mockResolvedValue({});
    const paused = { ...running, state: 'paused' as const, can_pause: false, can_resume: true, can_edit: true };
    render(<TopToolbar {...toolbarProps()} workflowControl={paused} />);
    await waitFor(() => expect(mocks.sendRequest).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: 'Resume' })).toHaveAttribute('data-variant', 'invert');
  });

  it('draws Stop and Reset without a colour of their own', async () => {
    mocks.sendRequest.mockResolvedValue(withGlobalModel);
    const { container } = render(<TopToolbar {...toolbarProps()} />);
    expect(await screen.findByRole('button', { name: 'Apply All' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Reset' })).toBeEnabled();
    const dot = screen.getByText('Saved').querySelector('div');
    expect(dot).toHaveClass('bg-success');
    expect(coloured(container)).toEqual([dot]);
  });

  it('fills a panel toggle while its panel shows', async () => {
    mocks.sendRequest.mockResolvedValue({});
    render(<TopToolbar {...toolbarProps()} />);
    await waitFor(() => expect(mocks.sendRequest).toHaveBeenCalled());
    const workspace = screen.getByRole('button', { name: 'Workspace' });
    expect(workspace).toHaveAttribute('aria-pressed', 'false');
    expect(workspace).toHaveClass('aria-pressed:bg-bg-active');
    fireEvent.click(workspace);
    expect(workspace).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('TopToolbar credential responses during a workflow run', () => {
  it.each([
    ['an error payload', { success: false, error: 'Unable to load providers' }],
    ['a response without providers', {}],
  ])('keeps Stop and workspace usable when %s arrives after Start', async (_label, response) => {
    let resolveResponse!: (response: unknown) => void;
    const pendingResponse = new Promise((resolve) => { resolveResponse = resolve; });
    mocks.sendRequest.mockReturnValue(pendingResponse);
    const props = toolbarProps();
    const ready = { ...running, state: 'ready' as const, can_start: true, can_pause: false, can_edit: true };
    const view = render(<TopToolbar {...props} workflowControl={ready} />);
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled();
    await waitFor(() => expect(mocks.sendRequest).toHaveBeenCalledWith('get_validated_ai_providers', {}));

    view.rerender(<TopToolbar {...props} />);
    await act(async () => {
      resolveResponse(response);
      await pendingResponse;
    });

    expect(screen.queryByRole('button', { name: /^Set Global Model/ })).not.toBeInTheDocument();
    expect(screen.getByText('Locked')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(props.onPauseWorkflow).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Workspace' }));
    expect(useCanvasDockStore.getState().open).toBe(true);

    // A later successful credential refresh can restore the model picker.
    mocks.sendRequest.mockResolvedValue({ providers: [{ provider: 'openai', models: ['gpt-4.1'], offered: ['gpt-4.1'] }] });
    mocks.storedSignature = 'openai-added';
    view.rerender(<TopToolbar {...props} />);
    expect(await screen.findByRole('button', { name: 'Set Global Model Select a model' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled();
  });

  it('sets the global model from the model menu Home shows too, in no colour', async () => {
    mocks.sendRequest.mockImplementation(async (type: string) => type === 'save_global_model'
      ? { success: true }
      : {
          providers: [
            { provider: 'openai', display_name: 'OpenAI', models: ['gpt-4.1', 'gpt-4.1-mini'], offered: ['gpt-4.1', 'gpt-4.1-mini'] },
            { provider: 'openrouter', display_name: 'OpenRouter', models: [], offered: ['x-ai/grok-4.20'] },
          ],
          global_provider: 'openai',
          global_model: 'gpt-4.1',
        });
    const props = toolbarProps();
    const user = userEvent.setup();
    render(<TopToolbar {...props} />);

    await user.click(await screen.findByRole('button', { name: 'Set Global Model gpt-4.1' }));
    const menu = await screen.findByRole('dialog', { name: 'Choose the global model' });
    // Each provider's offered models under its name; the chosen one ticked.
    expect(within(within(menu).getByRole('group', { name: 'OpenAI' })).getAllByRole('option').map((row) => row.textContent)).toEqual([
      'gpt-4.1',
      'gpt-4.1-mini',
    ]);
    expect(within(within(menu).getByRole('group', { name: 'OpenRouter' })).getByRole('option')).toHaveTextContent('x-ai/grok-4.20');
    expect(within(menu).getByRole('option', { name: 'gpt-4.1' }).querySelector('svg')).not.toBeNull();
    // No provider carries a brand colour.
    expect(menu.querySelectorAll('[style*="color"]')).toHaveLength(0);

    await user.click(within(menu).getByRole('option', { name: 'gpt-4.1-mini' }));
    expect(props.onGlobalModelChange).toHaveBeenCalledWith('openai', 'gpt-4.1-mini');
    expect(mocks.sendRequest).toHaveBeenCalledWith('save_global_model', { provider: 'openai', model: 'gpt-4.1-mini' });
    expect(await screen.findByRole('button', { name: 'Set Global Model gpt-4.1-mini' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled();
  });

  it('closes the model menu without a pick, on Escape or a press anywhere, the canvas included', async () => {
    mocks.sendRequest.mockResolvedValue({
      providers: [{ provider: 'openai', display_name: 'OpenAI', models: ['gpt-4.1'], offered: ['gpt-4.1'] }],
      global_provider: 'openai',
      global_model: 'gpt-4.1',
    });
    const user = userEvent.setup();
    render(<TopToolbar {...toolbarProps()} />);
    const button = await screen.findByRole('button', { name: 'Set Global Model gpt-4.1' });

    await user.click(button);
    await screen.findByRole('dialog', { name: 'Choose the global model' });
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // The canvas stops a press's mousedown (React Flow pans on it), and a
    // popover the press never reached stays open. The menu is modal: while
    // it shows, nothing on the page takes a pointer event, so a press on
    // the canvas, the toolbar or the menu's own button lands on the page
    // itself and closes it.
    await user.click(button);
    await screen.findByRole('dialog', { name: 'Choose the global model' });
    expect(document.body.style.pointerEvents).toBe('none');
    await user.click(document.documentElement);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(document.body.style.pointerEvents).toBe('');
    expect(mocks.sendRequest).not.toHaveBeenCalledWith('save_global_model', expect.anything());
  });
});
