/**
 * The hire composer's model picker (hire/useHirePicker.ts, the employee
 * chat's composer/ModelPicker.tsx): no button until an AI model is set up;
 * the panel lists Auto (which model a hire gets without a pick), each
 * connected provider's models under its name, and the thinking levels; a
 * pick and a level are saved with the owner's settings (`hire_model` /
 * `hire_effort`); the button names the pick; and the job goes to the picked
 * model.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();
const connectors = { hasAi: true };

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

vi.mock('../../data/connectors', () => ({
  useConnectors: () => ({ providers: [], categories: [], connectedApps: [], isLoading: false, ...connectors }),
}));

vi.mock('../../ui/pillToast', () => ({ pillToast: vi.fn() }));

import { ThemeProvider } from '@/contexts/ThemeContext';
import { Composer } from '../../hire/Composer';
import { useHirePicker } from '../../hire/useHirePicker';
import { resetDraftForTests } from '../draftStore';
import { useHireComposer } from '../useHireComposer';

type Wire = Record<string, unknown>;

const SONNET = 'anthropic::claude-sonnet-5-5';

function row(id: string, providerName: string): Wire {
  const [provider, name] = id.split('::');
  return {
    id,
    name,
    short: name,
    description: '',
    provider,
    provider_name: providerName,
    effort: true,
    effort_note: null,
    available: true,
    reason: null,
  };
}

/** The rows as get_hire_models sends them. */
const PICKER = {
  success: true,
  auto: {
    id: 'auto',
    name: 'Auto',
    short: 'Auto',
    description: 'Uses gpt-x',
    provider: null,
    provider_name: null,
    effort: true,
    effort_note: null,
    available: true,
    reason: null,
  },
  models: [row(SONNET, 'Anthropic'), row('openai::gpt-x', 'OpenAI')],
  efforts: [
    { id: 'low', label: 'Quick', hint: 'Answers right away' },
    { id: '', label: 'Balanced', hint: 'A good mix of speed and care' },
    { id: 'high', label: 'Thorough', hint: 'Takes longer, double-checks the details' },
  ],
};

let settings: Wire;

/** The hire view's composer as the hire view binds it. */
function HireComposer() {
  const composer = useHireComposer();
  const picker = useHirePicker();
  return (
    <Composer
      value={composer.value}
      onChange={composer.onChange}
      onSubmit={composer.onSubmit}
      refining={composer.refining}
      onStopRefining={composer.onStopRefining}
      working={composer.working}
      apps={[]}
      onOpenApps={() => {}}
      picker={picker}
    />
  );
}

function renderComposer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <HireComposer />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

function sent(type: string): Wire[] {
  return sendRequest.mock.calls.filter(([name]) => name === type).map(([, data]) => data as Wire);
}

beforeAll(() => {
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  resetDraftForTests();
  connectors.hasAi = true;
  settings = { hire_model: 'auto', hire_effort: '' };
  sendRequest.mockReset().mockImplementation(async (type: string, data: Wire) => {
    if (type === 'get_hire_models') return PICKER;
    if (type === 'get_user_settings') return { settings };
    if (type === 'save_user_settings') {
      settings = { ...settings, ...(data.settings as Wire) };
      return { settings };
    }
    if (type === 'generate_employee_setup') return { success: false, error: 'timeout' };
    return { success: true };
  });
});

describe('the hire composer’s model picker', () => {
  it('has no button until an AI model is set up', async () => {
    connectors.hasAi = false;
    renderComposer();
    expect(await screen.findByRole('button', { name: /Create employee/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Auto' })).not.toBeInTheDocument();
    expect(sent('get_hire_models')).toEqual([]);
  });

  it('lists what a new hire can run on, and how hard it thinks', async () => {
    const user = userEvent.setup();
    renderComposer();
    await user.click(await screen.findByRole('button', { name: 'Auto' }));
    const panel = await screen.findByRole('dialog', { name: 'Choose how your new hire answers' });
    expect(within(panel).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'AutoUses gpt-x',
      'claude-sonnet-5-5',
      'gpt-x',
    ]);
    expect(within(panel).getByRole('group', { name: 'Anthropic' })).toBeInTheDocument();
    expect(within(panel).getByRole('slider', { name: 'How hard your new hire thinks' })).toHaveAttribute('aria-valuetext', 'Balanced');
  });

  it('saves a pick and a level, and the job goes to the picked model', async () => {
    const user = userEvent.setup();
    renderComposer();
    await user.click(await screen.findByRole('button', { name: 'Auto' }));
    const panel = await screen.findByRole('dialog', { name: 'Choose how your new hire answers' });
    await user.click(within(panel).getByRole('option', { name: 'claude-sonnet-5-5' }));
    expect(sent('save_user_settings')).toEqual([{ settings: { hire_model: SONNET } }]);

    await user.click(await screen.findByRole('button', { name: 'claude-sonnet-5-5' }));
    await screen.findByRole('dialog', { name: 'Choose how your new hire answers' });
    // The list has the focus: ArrowRight goes one level up.
    await user.keyboard('{ArrowRight}');
    expect(sent('save_user_settings')[1]).toEqual({ settings: { hire_effort: 'high' } });
    await user.keyboard('{Escape}');
    expect(await screen.findByRole('button', { name: 'claude-sonnet-5-5 · Thorough' })).toBeInTheDocument();
    // Closing puts the cursor back in the job box.
    expect(screen.getByRole('textbox', { name: 'Describe the job' })).toHaveFocus();

    await user.type(screen.getByRole('textbox', { name: 'Describe the job' }), 'Answer my WhatsApp');
    await user.click(screen.getByRole('button', { name: /Create employee/ }));
    await waitFor(() => expect(sent('generate_employee_setup')).toHaveLength(1));
    expect(sent('generate_employee_setup')[0]).toMatchObject({ job: 'Answer my WhatsApp', model: SONNET });
  });
});
