import { fireEvent, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders as render } from '../../../../test/providers';
import type { CanvasItem } from '../../../../lib/canvasBoard';

const { wsMock } = vi.hoisted(() => ({
  wsMock: { sendRequest: vi.fn(), isReady: true },
}));
vi.mock('../../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => wsMock,
  useWebSocketActions: () => wsMock,
}));

// Isolate iframe/sandbox assertions from real fetches.
const { textHookMock } = vi.hoisted(() => ({ textHookMock: vi.fn() }));
vi.mock('../../../../hooks/useWorkspaceText', () => ({
  TEXT_FETCH_CAP_BYTES: 512 * 1024,
  fetchWorkspaceText: vi.fn(),
  useWorkspaceTextQuery: (ref: unknown) => textHookMock(ref),
}));

import CanvasContent from '../CanvasContent';

const note = (id: string, content: string): CanvasItem => ({
  id,
  kind: 'note',
  title: null,
  ref: null,
  url: null,
  content,
  language: null,
  source: 'agent',
  created_at: null,
});

const urlItem = (id: string, url: string): CanvasItem => ({
  ...note(id, ''),
  kind: 'url',
  content: null,
  url,
});

const htmlItem = (id: string): CanvasItem => ({
  id,
  kind: 'file',
  title: null,
  ref: {
    kind: 'file',
    path: 'reports/page.html',
    filename: 'page.html',
    mime_type: 'text/html',
    workflow_id: 'wf-1',
    url: '/api/workspace/wf-1/files/reports/page.html',
  },
  url: null,
  content: null,
  language: null,
  source: 'agent',
  created_at: null,
});

describe('CanvasContent carousel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    textHookMock.mockReturnValue({
      data: { text: '<h1>report</h1>', truncated: false },
      isLoading: false,
      isError: false,
    });
  });

  it('shows the newest item by default and follows new pushes', () => {
    const items = [note('a', 'first'), note('b', 'second')];
    const { rerender } = render(<CanvasContent items={items} workflowId="wf-1" />);
    expect(screen.getByText('second')).toBeInTheDocument();

    // Unpinned = stick to newest: a pushed item surfaces automatically.
    rerender(<CanvasContent items={[...items, note('c', 'third')]} workflowId="wf-1" />);
    expect(screen.getByText('third')).toBeInTheDocument();
  });

  it('pins on prev, stays pinned across pushes, resumes follow at the end', () => {
    const items = [note('a', 'first'), note('b', 'second')];
    const { rerender } = render(<CanvasContent items={items} workflowId="wf-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Previous item' }));
    expect(screen.getByText('first')).toBeInTheDocument();

    // Pinned: a new push does NOT yank the view.
    const grown = [...items, note('c', 'third')];
    rerender(<CanvasContent items={grown} workflowId="wf-1" />);
    expect(screen.getByText('first')).toBeInTheDocument();
    expect(screen.getByText('1/3')).toBeInTheDocument();

    // Navigating to the last item resumes follow-newest.
    fireEvent.click(screen.getByRole('button', { name: 'Next item' }));
    fireEvent.click(screen.getByRole('button', { name: 'Next item' }));
    rerender(<CanvasContent items={[...grown, note('d', 'fourth')]} workflowId="wf-1" />);
    expect(screen.getByText('fourth')).toBeInTheDocument();
  });

  it('navigates with arrow keys on the focused group only', () => {
    render(
      <CanvasContent items={[note('a', 'first'), note('b', 'second')]} workflowId="wf-1" />,
    );
    const group = screen.getByRole('group', { name: 'Canvas items' });
    fireEvent.keyDown(group, { key: 'ArrowLeft' });
    expect(screen.getByText('first')).toBeInTheDocument();
    fireEvent.keyDown(group, { key: 'ArrowRight' });
    expect(screen.getByText('second')).toBeInTheDocument();
  });

  it('reports the active item id on remove', () => {
    const onRemove = vi.fn();
    render(
      <CanvasContent
        items={[note('a', 'first'), note('b', 'second')]}
        workflowId="wf-1"
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Remove item' }));
    expect(onRemove).toHaveBeenCalledWith('b');
  });

  it('renders the empty hint when the board is empty', () => {
    render(<CanvasContent items={[]} workflowId="wf-1" emptyHint="Board is empty" />);
    expect(screen.getByText('Board is empty')).toBeInTheDocument();
  });
});

describe('CanvasContent sandbox regression (security invariants)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    textHookMock.mockReturnValue({
      data: { text: '<h1>report</h1>', truncated: false },
      isLoading: false,
      isError: false,
    });
  });

  it('external URLs get exactly allow-scripts allow-forms and no referrer', () => {
    const { container } = render(
      <CanvasContent items={[urlItem('u', 'https://example.com')]} workflowId="wf-1" />,
    );
    const iframe = container.querySelector('iframe') as HTMLIFrameElement;
    expect(iframe).toBeTruthy();
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts allow-forms');
    expect(iframe.getAttribute('referrerpolicy')).toBe('no-referrer');
    // The escape hatch must always be visible — framing denial is undetectable.
    expect(screen.getByRole('link', { name: /Open in new tab/ })).toBeInTheDocument();
  });

  it('workspace HTML renders via srcDoc WITHOUT allow-same-origin', () => {
    const { container } = render(
      <CanvasContent items={[htmlItem('h')]} workflowId="wf-1" />,
    );
    const iframe = container.querySelector('iframe') as HTMLIFrameElement;
    expect(iframe).toBeTruthy();
    expect(iframe.getAttribute('srcdoc')).toContain('<h1>report</h1>');
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin');
    // Never a src pointing at the app origin for HTML.
    expect(iframe.getAttribute('src')).toBeNull();
  });
});
