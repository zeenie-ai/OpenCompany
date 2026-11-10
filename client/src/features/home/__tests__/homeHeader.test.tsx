/**
 * The Home header: on an employee's page it says who they are (their face
 * on the accent disc, name, role and apps, status) and offers New
 * conversation, which asks first; the owner renames them and changes their
 * photo there; the Get started pill stands in for the checklist card there
 * only; the Normal/Dev switch opens that employee's workflow there, and
 * elsewhere what the editor last had.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';

const sendRequest = vi.fn();

vi.mock('@/contexts/WebSocketContext', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/WebSocketContext')>()),
  useWebSocketActions: () => ({ sendRequest, isReady: true, addEventListener: () => () => {} }),
}));
vi.mock('../../../app/useShellActions', () => ({ enterDev: vi.fn(), enterNormal: vi.fn() }));
vi.mock('../../../app/ShellModeSwitch', () => ({ useShellMode: () => 'normal' }));
vi.mock('../workspace/WorkspaceButton', () => ({ WorkspaceButton: () => null }));
vi.mock('@/components/ui/ThemeButton', () => ({ ThemeButton: () => null }));
vi.mock('../onboarding/GetStartedPill', () => ({ GetStartedPill: () => <span>Get started pill</span> }));
vi.mock('../ui/pillToast', () => ({ pillToast: vi.fn() }));
vi.mock('@/lib/workspaceUpload', () => ({ uploadToWorkspace: vi.fn() }));

import { normalizeWorkflowControlStatus } from '@/contexts/WebSocketContext';
import { uploadToWorkspace } from '@/lib/workspaceUpload';
import { useNodeStatusStore } from '@/stores/nodeStatusStore';
import { enterDev } from '../../../app/useShellActions';
import { parseEmployee } from '../data/schemas';
import { getDefaultEmployeeAvatar } from '../data/avatars';
import { HomeHeader } from '../header/HomeHeader';
import { useHomeStore } from '../state/homeStore';
import { pillToast } from '../ui/pillToast';

const maya = parseEmployee({
  workflow_id: 'w1',
  name: 'Maya',
  role: 'Receptionist',
  status: 'working',
  talk: { state: 'on', agent_node_id: 'w1:talk' },
  apps: [
    { app_id: 'whatsapp', provider_id: 'whatsapp', name: 'WhatsApp', connected: true },
    { app_id: 'calendar', provider_id: 'google', name: 'Google Calendar', connected: true },
  ],
  control: normalizeWorkflowControlStatus({ generation: 1, revision: 2, state: 'running' }, 'w1'),
})!;

function wrap(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{children}</QueryClientProvider>);
}

describe('HomeHeader', () => {
  beforeEach(() => {
    vi.mocked(enterDev).mockClear();
    sendRequest.mockReset().mockResolvedValue({ success: true });
    useHomeStore.setState({ sidebarOpen: true });
    useNodeStatusStore.setState({ allStatuses: {} });
  });

  it('says who is on screen', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    expect(screen.getByRole('heading', { name: 'Maya' })).toBeInTheDocument();
    expect(screen.getByText('Receptionist · WhatsApp, Google Calendar')).toBeInTheDocument();
    // Running with nothing to do: Ready.
    expect(screen.getByText('Ready')).toBeInTheDocument();
  });

  it('says Working while their talk agent answers', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    useNodeStatusStore.setState({ allStatuses: { w1: { 'w1:talk': { status: 'executing' } } } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    expect(screen.getByText('Working')).toBeInTheDocument();
    expect(screen.queryByText('Ready')).not.toBeInTheDocument();
  });

  it('starts a new conversation once the owner confirms, which ends a first day', async () => {
    const user = userEvent.setup();
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' }, firstDays: { w1: { started: true, nodeCount: 6 } } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    await user.click(screen.getByRole('button', { name: 'New conversation' }));
    expect(await screen.findByRole('alertdialog')).toHaveTextContent('This clears the conversation, and Maya forgets it too.');
    expect(sendRequest).not.toHaveBeenCalledWith('clear_chat_messages', expect.anything());
    await user.click(screen.getByRole('button', { name: 'New conversation' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('clear_chat_messages', { session_id: 'w1' }));
    await waitFor(() => expect(useHomeStore.getState().firstDays).toEqual({}));
  });

  it('offers no new conversation without a talk line', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={{ ...maya, talk: { state: 'off', agent_node_id: null } }} scrolled={false} />);
    expect(screen.queryByRole('button', { name: 'New conversation' })).not.toBeInTheDocument();
  });

  it('opens the employee on screen in Dev mode', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    fireEvent.click(screen.getByRole('radio', { name: 'Dev' }));
    expect(enterDev).toHaveBeenCalledWith({ workflowId: 'w1' });
  });

  it('renames them from the pencil beside their name', async () => {
    const user = userEvent.setup();
    let answer: (value: unknown) => void = () => {};
    sendRequest.mockImplementation((type: string) =>
      type === 'rename_employee' ? new Promise((resolve) => (answer = resolve)) : Promise.resolve({ success: true }),
    );
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    await user.click(screen.getByRole('button', { name: 'Rename Maya' }));
    const box = screen.getByRole('textbox', { name: 'Name' });
    expect(box).toHaveValue('Maya');
    await user.clear(box);
    await user.type(box, '  Ana   Lopez {Enter}');
    expect(sendRequest).toHaveBeenCalledWith('rename_employee', { workflow_id: 'w1', name: 'Ana Lopez' });
    // The new name shows while it saves.
    expect(screen.getByRole('heading', { name: 'Ana Lopez' })).toBeInTheDocument();
    answer({ success: true });
  });

  it('keeps the name when the owner presses Escape', async () => {
    const user = userEvent.setup();
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    await user.click(screen.getByRole('button', { name: 'Rename Maya' }));
    await user.type(screen.getByRole('textbox', { name: 'Name' }), ' Lopez{Escape}');
    expect(screen.getByRole('heading', { name: 'Maya' })).toBeInTheDocument();
    expect(sendRequest).not.toHaveBeenCalledWith('rename_employee', expect.anything());
  });

  it('gives a hired employee a photo they upload', async () => {
    const user = userEvent.setup();
    vi.mocked(uploadToWorkspace).mockResolvedValue({ path: 'uploads/me.png' } as Awaited<ReturnType<typeof uploadToWorkspace>>);
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={{ ...maya, derived: false }} scrolled={false} />);
    await user.click(screen.getByRole('button', { name: 'Change Maya’s photo' }));
    expect(screen.queryByRole('menuitem', { name: 'Remove photo' })).not.toBeInTheDocument();
    await user.click(await screen.findByRole('menuitem', { name: 'Upload photo…' }));
    const photo = new File(['png'], 'me.png', { type: 'image/png' });
    await user.upload(screen.getByTestId('employee-photo-input'), photo);
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('set_employee_photo', { workflow_id: 'w1', path: 'uploads/me.png' }));
    expect(uploadToWorkspace).toHaveBeenCalledWith(photo, 'w1');
  });

  it('refuses a file that is not a photo before uploading it', async () => {
    vi.mocked(uploadToWorkspace).mockClear();
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={{ ...maya, derived: false }} scrolled={false} />);
    fireEvent.change(screen.getByTestId('employee-photo-input'), { target: { files: [new File(['x'], 'notes.txt', { type: 'text/plain' })] } });
    await waitFor(() => expect(pillToast).toHaveBeenCalledWith('A photo is a PNG, JPEG, WebP or GIF image.', { tone: 'error' }));
    expect(uploadToWorkspace).not.toHaveBeenCalled();
    expect(sendRequest).not.toHaveBeenCalledWith('set_employee_photo', expect.anything());
  });

  it('shows the uploaded photo, offers removal, and falls back to the assigned cat when it will not load', async () => {
    const user = userEvent.setup();
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    const photoUrl = '/api/workspace/w1/files/uploads/me.png?v=1';
    const { container } = wrap(<HomeHeader title="Maya" employee={{ ...maya, derived: false, photo_url: photoUrl }} scrolled={false} />);
    const image = container.querySelector('img');
    expect(image).toHaveAttribute('src', photoUrl);
    expect(image!.closest('.bg-accent')).not.toBeNull();
    await user.click(screen.getByRole('button', { name: 'Change Maya’s photo' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Remove photo' }));
    await waitFor(() => expect(sendRequest).toHaveBeenCalledWith('set_employee_photo', { workflow_id: 'w1', path: null }));
    fireEvent.error(image!);
    expect(container.querySelector('img')).toHaveAttribute('src', getDefaultEmployeeAvatar('w1'));
  });

  it('gives an employee built in the editor an assigned portrait without a photo-upload menu', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    const { container } = wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    expect(screen.queryByRole('button', { name: 'Change Maya’s photo' })).not.toBeInTheDocument();
    expect(container.querySelector('img')).toHaveAttribute('src', getDefaultEmployeeAvatar('w1'));
    expect(container.querySelector('img')?.parentElement).toHaveClass('bg-accent', 'border-accent-edge');
  });

  it('draws their face on the accent disc, which dims under the pointer', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={{ ...maya, color_role: 'tool', derived: false }} scrolled={false} />);
    const trigger = screen.getByRole('button', { name: 'Change Maya’s photo' });
    const face = trigger.querySelector('img')!.parentElement!;
    expect(face).toHaveClass('bg-accent', 'text-accent-foreground', 'border-accent-edge', 'shadow-avatar');
    expect(face.className).not.toMatch(/node-tool/);
    expect(trigger).toHaveClass('hover:opacity-88');
  });

  it('offers the Get started pill on an employee’s page only, where the card would cover Send', () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    const { unmount } = wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
    expect(screen.getByText('Get started pill')).toBeInTheDocument();
    unmount();
    // The hire view has the card itself.
    useHomeStore.setState({ view: { kind: 'hire' } });
    wrap(<HomeHeader title="New employee" employee={null} scrolled={false} />);
    expect(screen.queryByText('Get started pill')).not.toBeInTheDocument();
  });

  it('opens the Welcome guide at its first step', () => {
    useHomeStore.setState({
      view: { kind: 'hire' },
      guide: { open: false, step: 'connect', furthest: 1, checked: true, provider: null, pendingDraft: false },
    });
    wrap(<HomeHeader title="New employee" employee={null} scrolled={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Guide' }));
    expect(useHomeStore.getState().guide).toMatchObject({ open: true, step: 'welcome', furthest: 1 });
  });

  it('opens what the editor last had from the hire view', () => {
    useHomeStore.setState({ view: { kind: 'hire' } });
    wrap(<HomeHeader title="New employee" employee={null} scrolled={false} />);
    expect(screen.getByRole('heading', { name: 'New employee' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Dev' }));
    expect(enterDev).toHaveBeenCalledWith({ workflowId: undefined });
  });
});

/**
 * The header fits the page it has: the Workspace dock takes room from the
 * page, so the header watches its own width, not the window's.
 */
describe('HomeHeader on a narrow page', () => {
  beforeEach(() => {
    sendRequest.mockReset().mockResolvedValue({ success: true });
    useHomeStore.setState({ sidebarOpen: true });
    useNodeStatusStore.setState({ allStatuses: {} });
  });

  const header = () => document.querySelector('header') as HTMLElement;
  /** The part that watches its own width: who is on screen, and the buttons. */
  const fitted = () => header().querySelector('[class~="@container"]') as HTMLElement;
  const onHire = () => {
    useHomeStore.setState({ view: { kind: 'hire' } });
    wrap(<HomeHeader title="New employee" employee={null} scrolled={false} />);
  };
  const onMaya = () => {
    useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' } });
    wrap(<HomeHeader title="Maya" employee={maya} scrolled={false} />);
  };

  it('clips sideways, so nothing in it shows over the dock beside the page', () => {
    onHire();
    expect(header()).toHaveClass('overflow-x-clip');
  });

  it('drops its buttons’ words below 580px of its own width while hiring, and keeps the Guide pill', () => {
    onHire();
    expect(fitted()).toHaveClass('@container', 'min-w-0', 'flex-1', '@max-[580px]:[&_[data-label]]:sr-only');
    expect(fitted()).not.toHaveClass('@max-[1100px]:[&_[data-label]]:sr-only');
    const guide = screen.getByRole('button', { name: 'Guide' }).parentElement;
    expect(guide).toHaveClass('contents');
    expect(guide).not.toHaveClass('@max-[580px]:hidden');
  });

  it('drops them sooner on an employee’s page, where the Get started pill goes with them and the Guide pill last', () => {
    onMaya();
    expect(fitted()).toHaveClass('@container', '@max-[1100px]:[&_[data-label]]:sr-only');
    expect(fitted()).not.toHaveClass('@max-[580px]:[&_[data-label]]:sr-only');
    expect(screen.getByText('Get started pill').parentElement).toHaveClass('contents', '@max-[1100px]:hidden');
    expect(screen.getByRole('button', { name: 'Guide' }).parentElement).toHaveClass('contents', '@max-[580px]:hidden');
  });

  it.each([
    ['button', 'Guide'],
    ['button', 'New conversation'],
    ['radio', 'Normal'],
    ['radio', 'Dev'],
  ])('marks the word on the %s %s, so the icon and the name stay when the word goes', (role, name) => {
    onMaya();
    const control = screen.getByRole(role, { name });
    expect(control.querySelector('[data-label]')).toHaveTextContent(new RegExp(`^${name}$`));
    expect(control.querySelector('svg')).toBeInTheDocument();
  });

  it('watches the width beside the logo, which it keeps whole', () => {
    useHomeStore.setState({ sidebarOpen: false });
    onMaya();
    expect(fitted()).toContainElement(screen.getByRole('heading', { name: 'Maya' }));
    expect(fitted()).toContainElement(screen.getByRole('button', { name: 'Guide' }));
    expect(fitted()).not.toContainElement(screen.getByRole('button', { name: 'Open sidebar' }));
    expect(fitted().parentElement).toBe(header());
  });
});
