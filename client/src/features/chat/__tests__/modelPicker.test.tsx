/**
 * The model picker in Home's employee chat (composer/ModelPicker.tsx,
 * data/models.ts): the button names the model and a level other than
 * Balanced; the panel opens above the message box, level with its right
 * edge, and lists Auto and the offered models, a row the owner can't pick
 * saying why; picking or changing the level saves the owner's
 * choice at once and puts it back when the server refuses it; every message
 * carries the choice; a model that can't answer is refused in the server's
 * words; and a chat whose employee takes no choice shows no picker.
 */

import { StrictMode } from 'react';
import { beforeAll, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));

vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn() }));
vi.mock('@/features/home/ui/pillToast', () => ({ pillToast: vi.fn() }));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { presentEmployee } from '@/features/home/data/presentation';
import { parseEmployee, type EmployeeSummary } from '@/features/home/data/schemas';
import { EmployeeChat } from '@/features/home/employee/EmployeeChat';
import { pillToast } from '@/features/home/ui/pillToast';
import { USER_SETTINGS_QUERY_KEY } from '@/hooks/useUserSettingsQuery';
import { resetChatRunStore } from '@/stores/chatRunStore';
import { sendOptions } from '../data/send';
import { useComposerStore } from '../state/composerStore';

type Wire = Record<string, unknown>;

const SONNET = 'anthropic::claude-sonnet-5-5';
const GPT = 'openai::gpt-6-sol';
const HAIKU = 'anthropic::claude-haiku-4-5';

function model(id: string, name: string, short: string, patch: Wire = {}): Wire {
  return { id, name, short, description: `${name} is good`, effort: true, effort_note: null, available: true, reason: null, ...patch };
}

const PICKER = {
  success: true,
  session_id: 'w1',
  auto: model('auto', 'Auto', 'Auto', { description: 'Uses Sonnet 5.5, your default model' }),
  models: [
    model(SONNET, 'Claude Sonnet 5.5', 'Sonnet 5.5'),
    model(HAIKU, 'Claude Haiku 4.5', 'Haiku 4.5', { effort: false, effort_note: 'Haiku 4.5 sets its own pace' }),
    model(GPT, 'GPT-6 Sol', 'GPT-6 Sol', {
      available: false,
      reason: 'GPT-6 Sol can’t answer right now: OpenAI isn’t connected.',
    }),
  ],
  efforts: [
    { id: 'low', label: 'Quick', hint: 'Answers right away' },
    { id: '', label: 'Balanced', hint: 'A good mix of speed and care' },
    { id: 'high', label: 'Thorough', hint: 'Takes longer, double-checks the details' },
  ],
};

let server: { settings: Wire; modelChoice: boolean; refuseSave: Wire | null; send: Wire; sent: Wire[] };
let client: QueryClient;

function employee(): EmployeeSummary {
  return parseEmployee({
    workflow_id: 'w1',
    name: 'Maya',
    role: 'Receptionist',
    status: 'working',
    talk: { state: 'on', agent_node_id: 'w1:talk' },
    control: normalizeWorkflowControlStatus({ generation: 1, revision: 4, state: 'running' }, 'w1'),
  })!;
}

function renderChat({ strict = false }: { strict?: boolean } = {}) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const summary = employee();
  const view = presentEmployee(summary);
  const chat = (
    <QueryClientProvider client={client}>
      <EmployeeChat employee={summary} control={{ view, label: 'Start', busy: false, act: vi.fn() }} />
    </QueryClientProvider>
  );
  render(strict ? <StrictMode>{chat}</StrictMode> : chat);
}

async function picker() {
  return screen.findByRole('button', { name: /Sonnet 5\.5|Auto|Haiku 4\.5/ });
}

async function write(text: string) {
  fireEvent.change(await screen.findByRole('textbox', { name: 'Message Maya' }), { target: { value: text } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled());
}

beforeAll(() => {
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  resetChatRunStore();
  useComposerStore.setState({ drafts: {}, web: {} });
  vi.mocked(pillToast).mockClear();
  server = {
    settings: { chat_model: 'auto', chat_effort: '' },
    modelChoice: true,
    refuseSave: null,
    send: { success: true, message_id: 'm_new', run_id: 'r1', delivery: 'now' },
    sent: [],
  };
  sendRequest.mockReset().mockImplementation(async (type: string, data: Wire) => {
    if (type === 'chat_subscribe') return { success: true, hub_epoch: 'e1', active_runs: [] };
    if (type === 'get_chat_messages') return { success: true, messages: [] };
    if (type === 'get_chat_context') {
      return { success: true, commands: [], capabilities: { attachments: false, web: false, model_choice: server.modelChoice } };
    }
    if (type === 'get_chat_models') return PICKER;
    if (type === 'get_user_settings') return { settings: server.settings };
    if (type === 'save_user_settings') {
      if (server.refuseSave) return server.refuseSave;
      server.settings = { ...server.settings, ...(data.settings as Wire) };
      return { settings: server.settings };
    }
    if (type === 'send_chat_message') {
      server.sent.push(data);
      return server.send;
    }
    return { success: true };
  });
});

describe('the picker', () => {
  it('names the model and lists what the owner can pick', async () => {
    const user = userEvent.setup();
    renderChat();
    const button = await screen.findByRole('button', { name: 'Auto' });
    expect(button).toHaveAttribute('aria-haspopup', 'dialog');
    await user.click(button);
    const panel = await screen.findByRole('dialog', { name: 'Choose how Maya answers' });
    expect(sendRequest).toHaveBeenCalledWith('get_chat_models', { session_id: 'w1' });
    const rows = within(panel).getAllByRole('option');
    expect(rows.map((row) => row.textContent)).toEqual([
      'AutoUses Sonnet 5.5, your default model',
      'Claude Sonnet 5.5Claude Sonnet 5.5 is good',
      'Claude Haiku 4.5Claude Haiku 4.5 is good',
      'GPT-6 SolGPT-6 Sol can’t answer right now: OpenAI isn’t connected.',
    ]);
    expect(rows[3]).toHaveAttribute('aria-disabled', 'true');
    expect(within(panel).getByRole('slider', { name: 'How hard Maya thinks' })).toHaveAttribute('aria-valuetext', 'Balanced');
  });

  it('opens its panel above the message box, level with its right edge', async () => {
    // The box is the panel's anchor, under StrictMode too, which mounts the
    // button twice. Only the box measures anything here (nothing detached
    // does), the panel itself measures nothing, and the window is 1280x800
    // (jsdom's is 0x0, which every placement overflows).
    const spy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const [x, y, width, height] = this.classList.contains('chat-composer') ? [100, 600, 640, 120] : [0, 0, 0, 0];
      return { x, y, width, height, top: y, left: x, right: x + width, bottom: y + height, toJSON: () => ({}) } as DOMRect;
    });
    const page = document.documentElement;
    Object.defineProperty(page, 'clientWidth', { configurable: true, value: 1280 });
    Object.defineProperty(page, 'clientHeight', { configurable: true, value: 800 });
    onTestFinished(() => {
      spy.mockRestore();
      Reflect.deleteProperty(page, 'clientWidth');
      Reflect.deleteProperty(page, 'clientHeight');
    });
    const user = userEvent.setup();
    renderChat({ strict: true });
    await user.click(await screen.findByRole('button', { name: 'Auto' }));
    await screen.findByRole('dialog', { name: 'Choose how Maya answers' });
    const placed = document.querySelector<HTMLElement>('[data-radix-popper-content-wrapper]');
    await waitFor(() => expect(placed?.style.getPropertyValue('--radix-popper-anchor-width')).toBe('640px'));
    // 8px above the box's top, its right edge on the box's.
    expect(placed?.style.transform).toBe('translate(740px, 592px)');
  });

  it('saves a pick and every message carries it', async () => {
    const user = userEvent.setup();
    renderChat();
    await user.click(await screen.findByRole('button', { name: 'Auto' }));
    const panel = await screen.findByRole('dialog', { name: 'Choose how Maya answers' });
    await user.click(within(panel).getByRole('option', { name: /Claude Sonnet 5\.5/ }));
    expect(sendRequest).toHaveBeenCalledWith('save_user_settings', { settings: { chat_model: SONNET } });
    expect(await screen.findByRole('button', { name: 'Sonnet 5.5' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    await write('Book Saturday');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(server.sent).toHaveLength(1));
    expect(server.sent[0].options).toEqual({ model: SONNET });
  });

  it('changes how hard the model thinks from the keyboard', async () => {
    const user = userEvent.setup();
    server.settings = { chat_model: SONNET, chat_effort: '' };
    renderChat();
    await user.click(await screen.findByRole('button', { name: 'Sonnet 5.5' }));
    await screen.findByRole('dialog', { name: 'Choose how Maya answers' });
    // The list has the focus: ArrowRight goes one level up.
    await user.keyboard('{ArrowRight}');
    expect(sendRequest).toHaveBeenCalledWith('save_user_settings', { settings: { chat_effort: 'high' } });
    expect(await screen.findByRole('slider', { name: 'How hard Maya thinks' })).toHaveAttribute('aria-valuetext', 'Thorough');
    await user.keyboard('{Escape}');
    expect(await screen.findByRole('button', { name: 'Sonnet 5.5 · Thorough' })).toBeInTheDocument();
    // Closing puts the cursor back in the message box.
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toHaveFocus();

    await write('Check twice');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(server.sent).toHaveLength(1));
    expect(server.sent[0].options).toEqual({ model: SONNET, effort: 'high' });
  });

  it('says when a model sets its own pace', async () => {
    const user = userEvent.setup();
    server.settings = { chat_model: HAIKU, chat_effort: 'high' };
    renderChat();
    // No level on the button: Haiku takes none.
    await user.click(await screen.findByRole('button', { name: 'Haiku 4.5' }));
    const panel = await screen.findByRole('dialog', { name: 'Choose how Maya answers' });
    expect(within(panel).getByText('Haiku 4.5 sets its own pace')).toBeInTheDocument();
    expect(within(panel).queryByRole('slider')).not.toBeInTheDocument();
  });

  it('puts a refused pick back and says why', async () => {
    const user = userEvent.setup();
    server.refuseSave = { success: false, error: 'chat_choice_refused', detail: 'That model isn’t offered any more. Pick another one.' };
    renderChat();
    await user.click(await screen.findByRole('button', { name: 'Auto' }));
    const panel = await screen.findByRole('dialog', { name: 'Choose how Maya answers' });
    await user.click(within(panel).getByRole('option', { name: /Claude Sonnet 5\.5/ }));
    await waitFor(() =>
      expect(pillToast).toHaveBeenCalledWith('That model isn’t offered any more. Pick another one.', { tone: 'error' }),
    );
    expect(await screen.findByRole('button', { name: 'Auto' })).toBeInTheDocument();
    expect(client.getQueryData<Wire>(USER_SETTINGS_QUERY_KEY)?.chat_model).toBe('auto');
  });

  it('tells the owner, in the server’s words, when the model can’t answer', async () => {
    const user = userEvent.setup();
    server.send = { success: false, error: 'model_unavailable', detail: 'Your default model can’t answer right now.' };
    renderChat();
    await picker();
    await write('Hello');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('Your default model can’t answer right now.', { tone: 'error' }));
    expect(server.sent[0].options).toEqual({ model: 'auto' });
    // The words go back in the box.
    expect(screen.getByRole('textbox', { name: 'Message Maya' })).toHaveValue('Hello');
  });

  it('is not there when the employee takes no choice', async () => {
    const user = userEvent.setup();
    server.modelChoice = false;
    renderChat();
    await write('Hello');
    expect(screen.queryByRole('button', { name: 'Auto' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(server.sent).toHaveLength(1));
    expect(server.sent[0]).not.toHaveProperty('options');
    expect(sendRequest).not.toHaveBeenCalledWith('get_chat_models', expect.anything());
  });
});

describe('what a message carries', () => {
  it('builds the options from Web and the choice', () => {
    expect(sendOptions(true, null)).toBeUndefined();
    expect(sendOptions(false, null)).toEqual({ web: false });
    expect(sendOptions(true, { model: 'auto', effort: '' })).toEqual({ model: 'auto' });
    expect(sendOptions(false, { model: SONNET, effort: 'low' })).toEqual({ web: false, model: SONNET, effort: 'low' });
  });

  it('puts the settings back when a save fails in transit', async () => {
    const { useSaveUserSettingsMutationCore } = await import('@/hooks/useUserSettingsQuery');
    const { renderHook } = await import('@testing-library/react');
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(USER_SETTINGS_QUERY_KEY, { chat_model: 'auto' });
    const failing = vi.fn(async () => {
      throw new Error('socket closed');
    });
    const { result } = renderHook(() => useSaveUserSettingsMutationCore(failing), {
      wrapper: ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>,
    });
    await act(async () => {
      await result.current.mutateAsync({ chat_model: SONNET }).catch(() => undefined);
    });
    expect(queryClient.getQueryData<Wire>(USER_SETTINGS_QUERY_KEY)?.chat_model).toBe('auto');
  });
});
