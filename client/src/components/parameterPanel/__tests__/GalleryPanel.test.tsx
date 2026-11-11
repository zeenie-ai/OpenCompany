import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../../test/providers';
import type { WorkspaceEntry } from '../../../types/workspaceFiles';

const { wsMock } = vi.hoisted(() => ({ wsMock: { sendRequest: vi.fn() } }));
vi.mock('../../../contexts/WebSocketContext', () => ({ useWebSocket: () => wsMock }));

import GalleryPanel from '../GalleryPanel';

const dir = (name: string): WorkspaceEntry => ({
  name, path: name, is_dir: true, size_bytes: 0,
  modified_at: '2026-07-20T10:00:00', mime_type: null, url: null,
  preview: 'none', ref: null,
});

// Mirrors what the server sends: the ref and the preview verdict arrive
// with the row, so the panel never derives either.
const file = (path: string, mime = 'audio/wav'): WorkspaceEntry => {
  const name = path.split('/').pop() as string;
  const url = `/api/workspace/wf-1/files/${path}`;
  return {
    name,
    path,
    is_dir: false,
    size_bytes: 20844,
    modified_at: '2026-07-25T22:47:10',
    mime_type: mime,
    url,
    preview: 'audio',
    ref: {
      kind: 'file', path, workflow_id: 'wf-1', filename: name,
      mime_type: mime, size_bytes: 20844,
      modified_at: '2026-07-25T22:47:10', url,
    },
  };
};

const listing = (entries: WorkspaceEntry[], extra: Record<string, unknown> = {}) => ({
  success: true, workflow_id: 'wf-1', path: '', parent: null, crumbs: [],
  entries, count: entries.length, truncated: false,
  workspace_exists: true, path_exists: true, ...extra,
});

const noop = () => {};

describe('GalleryPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    wsMock.sendRequest.mockResolvedValue(listing([dir('audio'), file('greeting.wav')]));
  });

  it('lists the workspace and shows a size for files but not folders', async () => {
    render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={noop} />);

    expect(await screen.findByText('greeting.wav')).toBeInTheDocument();
    expect(screen.getByText('audio')).toBeInTheDocument();
    expect(screen.getByText('20 KB')).toBeInTheDocument();
    expect(screen.getByText('Folder')).toBeInTheDocument();
    expect(wsMock.sendRequest).toHaveBeenCalledWith('list_workspace_files', {
      workflow_id: 'wf-1', path: '', limit: 500,
    });
  });

  it('writes the node path when a folder is opened, so the node lists what you browse', async () => {
    const onParameterChange = vi.fn();
    render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={onParameterChange} />);

    fireEvent.click(await screen.findByText('audio'));

    expect(onParameterChange).toHaveBeenCalledWith('path', 'audio');
  });

  it('renders the breadcrumb trail the server computed', async () => {
    wsMock.sendRequest.mockResolvedValue(listing([file('audio/clips/one.wav')], {
      path: 'audio/clips',
      parent: 'audio',
      crumbs: [{ name: 'audio', path: 'audio' }, { name: 'clips', path: 'audio/clips' }],
    }));
    render(<GalleryPanel workflowId="wf-1" parameters={{ path: 'audio/clips' }} onParameterChange={noop} />);

    await waitFor(() => expect(wsMock.sendRequest).toHaveBeenCalledWith('list_workspace_files', {
      workflow_id: 'wf-1', path: 'audio/clips', limit: 500,
    }));
    // Not re-split from `path` here — the trail arrives ready to render.
    expect(await screen.findByRole('button', { name: 'audio' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'clips' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Workspace root' })).toBeInTheDocument();
  });

  it('pins a file into `selection` and unpins it again', async () => {
    const onParameterChange = vi.fn();
    const { rerender } = render(
      <GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={onParameterChange} />,
    );

    const pin = await screen.findByRole('button', { name: /Pin greeting\.wav/ });
    expect(pin).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(pin);
    expect(onParameterChange).toHaveBeenCalledWith('selection', ['greeting.wav']);

    rerender(
      <GalleryPanel
        workflowId="wf-1"
        parameters={{ selection: ['greeting.wav'] }}
        onParameterChange={onParameterChange}
      />,
    );
    const pinned = await screen.findByRole('button', { name: /Pin greeting\.wav/ });
    expect(pinned).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/pinned for this node's output/)).toBeInTheDocument();
    fireEvent.click(pinned);
    expect(onParameterChange).toHaveBeenLastCalledWith('selection', []);
  });

  it('pins without opening the file the pin sits on', async () => {
    const onParameterChange = vi.fn();
    render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={onParameterChange} />);

    const pin = await screen.findByRole('button', { name: /Pin greeting\.wav/ });
    fireEvent.click(pin);
    fireEvent.keyDown(pin, { key: 'Enter' });

    // Only the pin was written, and no preview opened over the panel.
    expect(onParameterChange).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('never calls the backend without a workflow, and says why', async () => {
    render(<GalleryPanel parameters={{}} onParameterChange={noop} />);

    expect(await screen.findByText(/Save this workflow to give it a workspace/)).toBeInTheDocument();
    expect(wsMock.sendRequest).not.toHaveBeenCalled();
  });

  it('distinguishes "no workspace yet" from "empty folder"', async () => {
    wsMock.sendRequest.mockResolvedValue(listing([], { workspace_exists: false }));
    const { unmount } = render(
      <GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={noop} />,
    );
    expect(await screen.findByText(/No workspace yet/)).toBeInTheDocument();
    unmount();

    wsMock.sendRequest.mockResolvedValue(listing([]));
    render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={noop} />);
    expect(await screen.findByText('This folder is empty.')).toBeInTheDocument();
  });

  it('surfaces a failed listing instead of showing an empty workspace', async () => {
    wsMock.sendRequest.mockResolvedValue({ success: false, error: 'outside this workflow' });
    render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={noop} />);

    expect(await screen.findByText('outside this workflow')).toBeInTheDocument();
  });

  it('sends the raw search term and lets the server decide what it means', async () => {
    render(<GalleryPanel workflowId="wf-1" parameters={{ path: 'audio' }} onParameterChange={noop} />);
    await screen.findByText('greeting.wav');

    fireEvent.change(screen.getByLabelText('Search this workspace'), { target: { value: 'greet' } });

    // No glob is assembled here: term-to-pattern is `search_to_pattern` on
    // the server, so the node and the panel cannot disagree about it.
    await waitFor(() => expect(wsMock.sendRequest).toHaveBeenCalledWith('list_workspace_files', {
      workflow_id: 'wf-1', path: 'audio', search: 'greet', limit: 500,
    }));
  });

  it('makes files draggable and folders not', async () => {
    render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={noop} />);
    await screen.findByText('greeting.wav');

    const tileOf = (label: string) =>
      screen.getByText(label).closest('[role="button"]') as HTMLElement;

    expect(tileOf('greeting.wav')).toHaveAttribute('draggable', 'true');
    expect(tileOf('audio')).toHaveAttribute('draggable', 'false');
  });

  // Colour marks state or role (docs-internal/theme_system.md): a file's kind
  // is told by its glyph's shape, and the panel's buttons are neutral.
  describe('the look', () => {
    it('has the node panels’ head, with a neutral Upload', async () => {
      render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={noop} />);
      await screen.findByText('greeting.wav');

      expect(screen.getByRole('heading', { name: 'Workspace Files' })).toHaveClass('font-display', 'text-md');
      const upload = screen.getByRole('button', { name: 'Upload' });
      expect(upload).toHaveAttribute('data-variant', 'quiet');
      expect(upload).toHaveClass('border-border-default', 'text-fg-default');
    });

    it('draws every glyph in the muted text colour, folders included', async () => {
      const { container } = render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={noop} />);
      await screen.findByText('greeting.wav');

      const tileOf = (label: string) => screen.getByText(label).closest('[role="button"]') as HTMLElement;
      expect(tileOf('audio').querySelector('svg')).toHaveClass('text-fg-muted');
      expect(tileOf('greeting.wav').querySelector('svg')).toHaveClass('text-fg-muted');
      expect(container.querySelector('[class*="text-node-"], [class*="text-action-"], [class*="bg-primary"]')).toBeNull();
    });

    it('counts what it lists, one file and one folder', async () => {
      render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={noop} />);

      expect(await screen.findByText('1 file · 1 folder')).toBeInTheDocument();
    });

    it('switches to a list, with sizes and dates in the mono face', async () => {
      render(<GalleryPanel workflowId="wf-1" parameters={{}} onParameterChange={noop} />);
      await screen.findByText('greeting.wav');

      expect(screen.getByRole('radio', { name: 'Grid view' })).toBeChecked();
      fireEvent.click(screen.getByRole('radio', { name: 'List view' }));

      expect(screen.getByRole('columnheader', { name: 'Modified' })).toBeInTheDocument();
      expect(screen.getByText('20 KB')).toHaveClass('font-mono', 'text-fg-muted');
      // The pin is offered on a file's row and never on a folder's.
      expect(screen.getAllByRole('button', { name: /^Pin / })).toHaveLength(1);

      // Clicking the picked layout again keeps it: there is always one.
      fireEvent.click(screen.getByRole('radio', { name: 'List view' }));
      expect(screen.getByRole('radio', { name: 'List view' })).toBeChecked();
    });
  });
});
