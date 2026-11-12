import { fireEvent, screen, within } from '@testing-library/react';
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
  HTML_FETCH_CAP_BYTES: 8 * 1024 * 1024,
  fetchWorkspaceText: vi.fn(),
  useWorkspaceTextQuery: (ref: unknown, capBytes?: number) => textHookMock(ref, capBytes),
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

/** An item under a name, which is what its chip on the Library strip says. */
const titled = (item: CanvasItem, title: string): CanvasItem => ({ ...item, title });

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

  it('pins on stepping back, stays pinned across pushes, resumes follow at the end', () => {
    const items = [titled(note('a', 'first'), 'One'), titled(note('b', 'second'), 'Two')];
    const { rerender } = render(<CanvasContent items={items} workflowId="wf-1" />);
    const group = screen.getByRole('group', { name: 'Canvas items' });

    fireEvent.keyDown(group, { key: 'ArrowLeft' });
    expect(screen.getByText('first')).toBeInTheDocument();

    // Pinned: a new push does NOT yank the view.
    const grown = [...items, titled(note('c', 'third'), 'Three')];
    rerender(<CanvasContent items={grown} workflowId="wf-1" />);
    expect(screen.getByText('first')).toBeInTheDocument();
    // The first of three: its chip is the one on show, and Latest is offered.
    expect(screen.getByRole('button', { name: 'Library, 3 items' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'One' })).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: 'Three' })).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('button', { name: 'Latest' })).toBeInTheDocument();

    // Navigating to the last item resumes follow-newest.
    fireEvent.keyDown(group, { key: 'ArrowRight' });
    expect(screen.getByText('second')).toBeInTheDocument();
    fireEvent.keyDown(group, { key: 'ArrowRight' });
    expect(screen.getByText('third')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Latest' })).not.toBeInTheDocument();
    rerender(<CanvasContent items={[...grown, titled(note('d', 'fourth'), 'Four')]} workflowId="wf-1" />);
    expect(screen.getByText('fourth')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Four' })).toHaveAttribute('aria-current', 'true');
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
    expect(textHookMock).toHaveBeenCalledWith(htmlItem('h').ref, 8 * 1024 * 1024);
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin');
    // Never a src pointing at the app origin for HTML.
    expect(iframe.getAttribute('src')).toBeNull();
  });
});

describe('CanvasContent HTML size limit', () => {
  it('offers a download instead of rendering an incomplete document above 8 MiB', () => {
    textHookMock.mockReturnValue({
      data: { text: '<h1>incomplete', truncated: true },
      isLoading: false,
      isError: false,
    });
    const { container } = render(
      <CanvasContent items={[htmlItem('large')]} workflowId="wf-1" />,
    );
    expect(screen.getByText(/8 MiB render cap/)).toBeInTheDocument();
    expect(container.querySelector('iframe')).toBeNull();
    expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute(
      'href', '/api/workspace/wf-1/files/reports/page.html',
    );
  });
});

describe('CanvasContent notes as documents', () => {
  const doc = (id: string, content: string, extra: Partial<CanvasItem> = {}): CanvasItem => ({
    ...note(id, content),
    ...extra,
  });

  const versionReply = (content: string, version: number, latest: number) => ({
    success: true,
    item: { ...doc('a', content), version, latest },
  });

  beforeEach(() => {
    vi.clearAllMocks();
    wsMock.sendRequest.mockReset();
  });

  it('shows the preview, or the Markdown source', () => {
    render(<CanvasContent items={[doc('a', '# Plan\n\nA **bold** step', { title: 'Plan' })]} workflowId="wf-1" />);
    expect(screen.getByRole('heading', { name: 'Plan' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Markdown' }));
    expect(screen.getByText(/A \*\*bold\*\* step/)).toBeInTheDocument();
  });

  it('previews a code note as a code block and downloads it as text', () => {
    const createObjectURL = vi.fn(() => 'blob:code');
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('script.txt');
    });
    const { container } = render(
      <CanvasContent items={[doc('a', 'print("```")', { title: 'script', language: 'Python' })]} workflowId="wf-1" />,
    );
    expect(container.querySelector('pre code')?.textContent).toBe('print("```")\n');
    expect(screen.getByRole('radio', { name: 'Source' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(click).toHaveBeenCalledTimes(1);
    expect((createObjectURL.mock.calls[0] as unknown as [Blob])[0].type).toBe('text/plain;charset=utf-8');
    click.mockRestore();
  });

  it.each(['html', ' HTM ', ' Text/HTML '])('previews an explicit %s note in isolation and preserves its source', (language) => {
    const content = '<!doctype html><html><body><h1 id="note-heading">Page</h1><script>document.body.dataset.ready = "yes";</script></body></html>';
    const { container } = render(
      <CanvasContent items={[doc('html', content, { title: 'Page', language })]} workflowId="wf-1" />,
    );

    const iframe = screen.getByTitle('Page preview');
    expect(iframe).toHaveAttribute('srcdoc', content);
    expect(iframe).toHaveAttribute('sandbox', 'allow-scripts');
    expect(iframe).not.toHaveAttribute('src');
    expect(container.querySelector('#note-heading')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('pre')).toBeNull();

    fireEvent.click(screen.getByRole('radio', { name: 'Source' }));
    expect(container.querySelector('iframe')).toBeNull();
    expect(container.querySelector('pre')?.textContent).toBe(content);
    fireEvent.click(screen.getByRole('radio', { name: 'Preview' }));
    expect(screen.getByTitle('Page preview')).toHaveAttribute('srcdoc', content);
  });

  it.each([null, 'markdown', 'python'])('keeps markup in a %s note out of the executable preview', (language) => {
    const { container } = render(
      <CanvasContent items={[doc('example', '<!doctype html><html><body><h1>Example</h1></body></html>', { language })]} workflowId="wf-1" />,
    );
    expect(container.querySelector('iframe')).toBeNull();
  });

  it.each([
    { opening: '```html', closing: '```', language: null, prefix: '' },
    { opening: '~~~~ HTM', closing: '~~~~~', language: 'markdown', prefix: 'Here is the page.\r\n\r\n' },
    { opening: '~~~Text/HTML', closing: '~~~', language: ' md ', prefix: '' },
  ])('previews a full $opening document and keeps surrounding commentary in Notes', ({ opening, closing, language, prefix }) => {
    const html = '<!DOCTYPE html>\r\n<html lang="en"><head><title>Artifact</title></head><body><h1>Artifact</h1></body></html>';
    const commentary = 'The page is ready to edit.\r\n\r\n- Uses inline CSS.';
    const content = `${prefix}${opening}\r\n${html}\r\n${closing}\r\n\r\n${commentary}`;
    const { container } = render(
      <CanvasContent items={[doc('artifact', content, { title: 'Artifact', language })]} workflowId="wf-1" />,
    );
    expect(screen.getByTitle('Artifact preview')).toHaveAttribute('srcdoc', html);
    expect(screen.getByTitle('Artifact preview')).toHaveAttribute('sandbox', 'allow-scripts');
    const notes = container.querySelector('details') as HTMLDetailsElement;
    expect(notes).not.toHaveAttribute('open');
    expect(within(notes).getByText('Notes')).toBeInTheDocument();
    expect(within(notes).getByText('The page is ready to edit.')).toBeInTheDocument();
    expect(within(notes).getByText('Uses inline CSS.')).toBeInTheDocument();
    if (prefix) expect(within(notes).getByText('Here is the page.')).toBeInTheDocument();
    expect(container.querySelector('pre')).toBeNull();

    fireEvent.click(screen.getByRole('radio', { name: 'Source' }));
    expect(container.querySelector('iframe')).toBeNull();
    expect(container.querySelector('pre')?.textContent).toBe(content);
  });

  it.each([
    ['small HTML example', 'Use a heading:\n\n```html\n<h1>Example</h1>\n```\n\nThis is an example.'],
    ['multiple HTML documents', '```html\n<html><body>One</body></html>\n```\n\n```html\n<html><body>Two</body></html>\n```'],
    ['HTML inside another code fence', '````markdown\n```html\n<html><body>Nested example</body></html>\n```\n````'],
    ['unclosed HTML fence', '```html\n<!doctype html><html><body>Incomplete fence</body></html>'],
    ['unlabeled code fence', '```\n<html><body>Example</body></html>\n```'],
  ])('keeps %s in the Markdown view', (_description, content) => {
    const { container } = render(<CanvasContent items={[doc('example', content)]} workflowId="wf-1" />);
    expect(container.querySelector('iframe')).toBeNull();
    expect(screen.getByRole('radio', { name: 'Markdown' })).toBeInTheDocument();
  });

  it('follows HTML revisions, holds an earlier HTML revision, and uses each selected revision', async () => {
    const firstHtml = '<html><body><h1>First</h1></body></html>';
    const first = doc('a', ['```html', firstHtml, '```', '', 'First commentary.'].join('\n'), { title: 'Page', version: 1 });
    const second = doc('a', '<h1>Second</h1>', { title: 'Page', language: 'html', version: 2 });
    wsMock.sendRequest.mockImplementation(async (_type: string, params: { version: number }) => ({
      success: true,
      item: { ...(params.version === 1 ? first : second), latest: 3 },
    }));
    const { rerender } = render(
      <CanvasContent items={[first]} workflowId="wf-1" nodeId="canvas-1" />,
    );
    expect(screen.getByTitle('Page preview')).toHaveAttribute('srcdoc', firstHtml);

    rerender(<CanvasContent items={[second]} workflowId="wf-1" nodeId="canvas-1" />);
    expect(screen.getByTitle('Page preview')).toHaveAttribute('srcdoc', second.content);
    fireEvent.click(screen.getByRole('button', { name: 'Previous version' }));
    expect(await screen.findByTitle('Page preview')).toHaveAttribute('srcdoc', firstHtml);

    const third = doc('a', '# Third', { title: 'Page', language: 'markdown', version: 3 });
    rerender(<CanvasContent items={[third]} workflowId="wf-1" nodeId="canvas-1" />);
    expect(screen.getByText('v1/3')).toBeInTheDocument();
    expect(screen.getByTitle('Page preview')).toHaveAttribute('srcdoc', firstHtml);
    expect(screen.getByText('First commentary.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Next version' }));
    expect(await screen.findByTitle('Page preview')).toHaveAttribute('srcdoc', second.content);
    fireEvent.click(screen.getByRole('button', { name: 'Next version' }));
    expect(screen.queryByTitle('Page preview')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Third' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Markdown' })).toBeInTheDocument();
  });

  it('copies and downloads the selected HTML version with its content, title, and MIME type', async () => {
    const html = '<!doctype html><html><body><h1>Earlier page</h1></body></html>';
    const content = ['```html', html, '```', '', 'This commentary belongs to the earlier version.'].join('\n');
    wsMock.sendRequest.mockResolvedValue({
      success: true,
      item: { ...doc('a', content, { title: 'Earlier page', version: 2 }), latest: 3 },
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const createObjectURL = vi.fn(() => 'blob:html');
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('Earlier-page-v2.html');
      expect(this.href).toBe('blob:html');
    });

    try {
      render(
        <CanvasContent
          items={[doc('a', '# Latest document', { title: 'Latest document', language: 'markdown', version: 3 })]}
          workflowId="wf-1"
          nodeId="canvas-1"
        />,
      );
      fireEvent.click(screen.getByRole('button', { name: 'Previous version' }));
      expect(await screen.findByTitle('Earlier page preview')).toHaveAttribute('srcdoc', html);
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
      expect(writeText).toHaveBeenCalledWith(content);
      expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Download' }));
      expect(click).toHaveBeenCalledTimes(1);
      const blob = (createObjectURL.mock.calls[0] as unknown as [Blob])[0];
      expect(blob.type).toBe('text/html;charset=utf-8');
      const downloaded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = () => reject(reader.error);
        reader.readAsText(blob);
      });
      expect(downloaded).toBe(html);
    } finally {
      click.mockRestore();
    }
  });

  it('steps back to an earlier version read from its node', async () => {
    wsMock.sendRequest.mockResolvedValue(versionReply('second draft', 2, 3));
    render(<CanvasContent items={[doc('a', 'final', { version: 3 })]} workflowId="wf-1" nodeId="canvas-1" />);
    expect(screen.getByText('v3/3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next version' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Previous version' }));
    expect(await screen.findByText('second draft')).toBeInTheDocument();
    expect(screen.getByText('v2/3')).toBeInTheDocument();
    expect(wsMock.sendRequest).toHaveBeenCalledWith('canvas_version', {
      workflow_id: 'wf-1',
      node_id: 'canvas-1',
      item_id: 'a',
      version: 2,
    });
  });

  it('loads a note’s images from its workflow’s workspace', () => {
    render(<CanvasContent items={[doc('a', '![Exhibit 1](charts/it_headcount.png)')]} workflowId="wf-1" />);

    expect(screen.getByRole('img', { name: 'Exhibit 1' })).toHaveAttribute(
      'src',
      '/api/workspace/wf-1/files/charts/it_headcount.png',
    );
  });

  it('loads a markdown file’s images from the file’s own folder', () => {
    textHookMock.mockReturnValue({
      data: { text: '![Revenue](../charts/revenue.png)', truncated: false },
      isLoading: false,
      isError: false,
    });
    const markdownFile: CanvasItem = {
      ...htmlItem('m'),
      ref: {
        kind: 'file',
        path: 'reports/summary.md',
        filename: 'summary.md',
        mime_type: 'text/markdown',
        workflow_id: 'wf-1',
        url: '/api/workspace/wf-1/files/reports/summary.md',
      },
    };
    render(<CanvasContent items={[markdownFile]} workflowId="wf-1" />);

    expect(screen.getByRole('img', { name: 'Revenue' })).toHaveAttribute(
      'src',
      '/api/workspace/wf-1/files/charts/revenue.png',
    );
  });

  it('shows only the latest without a node to read versions from', () => {
    render(<CanvasContent items={[doc('a', 'final', { version: 3 })]} workflowId="wf-1" />);
    expect(screen.queryByRole('button', { name: 'Previous version' })).not.toBeInTheDocument();
    expect(screen.getByText('final')).toBeInTheDocument();
  });

  it('follows a newer version unless the owner stepped back', async () => {
    wsMock.sendRequest.mockResolvedValue(versionReply('one', 1, 2));
    const { rerender } = render(
      <CanvasContent items={[doc('a', 'one', { version: 1 })]} workflowId="wf-1" nodeId="canvas-1" />,
    );
    rerender(<CanvasContent items={[doc('a', 'two', { version: 2 })]} workflowId="wf-1" nodeId="canvas-1" />);
    expect(screen.getByText('two')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Previous version' }));
    expect(await screen.findByText('one')).toBeInTheDocument();
    rerender(<CanvasContent items={[doc('a', 'three', { version: 3 })]} workflowId="wf-1" nodeId="canvas-1" />);
    expect(screen.getByText('v1/3')).toBeInTheDocument();
    expect(screen.queryByText('three')).not.toBeInTheDocument();
  });

  it('says so when a version cannot be read', async () => {
    wsMock.sendRequest.mockResolvedValue({ success: false, error: 'No such version' });
    render(<CanvasContent items={[doc('a', 'final', { version: 2 })]} workflowId="wf-1" nodeId="canvas-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Previous version' }));
    expect(await screen.findByText('Couldn’t load version 1.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy' })).toBeDisabled();
  });

  it('opens on the item and version a reply asks for, and holds it while others arrive', async () => {
    wsMock.sendRequest.mockResolvedValue(versionReply('draft of a', 1, 2));
    const items = [doc('a', 'a, final', { version: 2, title: 'Plan' }), doc('b', 'b', { title: 'Notes' })];
    const focus = { itemId: 'a', version: 1, nonce: 1 };
    const { rerender } = render(
      <CanvasContent items={items} workflowId="wf-1" nodeId="canvas-1" focus={focus} />,
    );
    expect(await screen.findByText('draft of a')).toBeInTheDocument();
    expect(screen.getByText('v1/2')).toBeInTheDocument();

    rerender(
      <CanvasContent items={[...items, doc('c', 'c', { title: 'Report' })]} workflowId="wf-1" nodeId="canvas-1" focus={focus} />,
    );
    expect(screen.getByText('draft of a')).toBeInTheDocument();
    // Still the first of three, with Latest offered.
    expect(screen.getByRole('button', { name: 'Library, 3 items' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Plan' })).toHaveAttribute('aria-current', 'true');
    expect(screen.getByRole('button', { name: 'Latest' })).toBeInTheDocument();

    // Moving on lets go of it: back on the item, the latest shows.
    const group = screen.getByRole('group', { name: 'Canvas items' });
    fireEvent.keyDown(group, { key: 'ArrowRight' });
    expect(screen.getByText('b')).toBeInTheDocument();
    fireEvent.keyDown(group, { key: 'ArrowLeft' });
    expect(screen.getByText('a, final')).toBeInTheDocument();
    expect(screen.getByText('v2/2')).toBeInTheDocument();
  });

  it('waits for the item a reply asks for to reach the board', () => {
    const focus = { itemId: 'b', version: null, nonce: 1 };
    const first = doc('a', 'a', { title: 'First' });
    const { rerender } = render(
      <CanvasContent items={[first]} workflowId="wf-1" nodeId="canvas-1" focus={focus} />,
    );
    expect(screen.getByText('a')).toBeInTheDocument();
    rerender(
      <CanvasContent
        items={[first, doc('b', 'b', { title: 'Second' }), doc('c', 'c', { title: 'Third' })]}
        workflowId="wf-1"
        nodeId="canvas-1"
        focus={focus}
      />,
    );
    expect(screen.getByText('b')).toBeInTheDocument();
    // The second of three, not the newest.
    expect(screen.getByRole('button', { name: 'Library, 3 items' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Second' })).toHaveAttribute('aria-current', 'true');
    expect(screen.queryByText('c')).not.toBeInTheDocument();
  });

  it('copies the text and downloads it as a .md file', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const createObjectURL = vi.fn(() => 'blob:doc');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('Weekly-report-v2.md');
      expect(this.href).toBe('blob:doc');
    });

    render(
      <CanvasContent
        items={[doc('a', '# Weekly report', { title: 'Weekly report!', version: 2 })]}
        workflowId="wf-1"
        nodeId="canvas-1"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('# Weekly report');
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(click).toHaveBeenCalledTimes(1);
    const blob = (createObjectURL.mock.calls[0] as unknown as [Blob])[0];
    expect(blob.type).toBe('text/markdown;charset=utf-8');
    click.mockRestore();
  });
});

// The one footer, in every host: the Library strip, then Remove item.
describe('CanvasContent library', () => {
  const image = (id: string, filename: string): CanvasItem => ({
    ...htmlItem(id),
    title: null,
    ref: {
      kind: 'image',
      path: `shots/${filename}`,
      filename,
      mime_type: 'image/png',
      workflow_id: 'wf-1',
      url: `/api/workspace/wf-1/files/shots/${filename}`,
      size_bytes: 2048,
    },
  });

  beforeEach(() => {
    vi.clearAllMocks();
    textHookMock.mockReturnValue({ data: { text: '', truncated: false }, isLoading: false, isError: false });
  });

  it('shows the six newest items on the strip and returns to the newest with Latest', () => {
    const items = Array.from({ length: 8 }, (_, index) => titled(note(`n${index}`, `body ${index}`), `Note ${index}`));
    render(<CanvasContent items={items} workflowId="wf-1" />);
    const chips = screen.getAllByRole('button', { name: /^Note \d$/ });
    expect(chips.map((chip) => chip.textContent)).toEqual(['MDNote 7', 'MDNote 6', 'MDNote 5', 'MDNote 4', 'MDNote 3', 'MDNote 2']);
    expect(chips[0]).toHaveAttribute('aria-current', 'true');
    expect(screen.queryByRole('button', { name: 'Latest' })).not.toBeInTheDocument();
    // No prev/next in the footer.
    expect(screen.queryByRole('button', { name: 'Previous item' })).not.toBeInTheDocument();

    fireEvent.click(chips[2]);
    expect(screen.getByText('body 5')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Latest' }));
    expect(screen.getByText('body 7')).toBeInTheDocument();
  });

  it('ends in the Library strip and Remove item, with no previous and next buttons, position counter or item label', () => {
    const onRemove = vi.fn();
    const items = [titled(note('a', 'first'), 'One'), titled(note('b', 'second'), 'Two')];
    render(<CanvasContent items={items} workflowId="wf-1" onRemove={onRemove} />);
    expect(screen.queryByRole('button', { name: 'Previous item' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Next item' })).not.toBeInTheDocument();
    expect(screen.queryByText('2/2')).not.toBeInTheDocument();
    // The footer is the last thing on the board. Its buttons, in order: the
    // Library, a chip for each item (the newest first), and Remove item,
    // which a host that can remove passes.
    const buttons = screen.getAllByRole('button');
    const footer = buttons.slice(buttons.indexOf(screen.getByRole('button', { name: 'Library, 2 items' })));
    expect(footer.map((button) => button.getAttribute('aria-label') ?? button.textContent)).toEqual([
      'Library, 2 items',
      'MDTwo',
      'MDOne',
      'Remove item',
    ]);
    // An item's name is on its chip only.
    expect(screen.getAllByText('Two')).toHaveLength(1);
  });

  it('shows no Remove item where the host passes no way to remove', () => {
    render(<CanvasContent items={[note('a', 'first'), note('b', 'second')]} workflowId="wf-1" />);
    expect(screen.getByRole('button', { name: 'Library, 2 items' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove item' })).not.toBeInTheDocument();
  });

  it('lists artifacts and files in the Library and opens one', () => {
    const items = [titled(note('n1', 'the plan'), 'Weekly plan'), image('i1', 'booking.png'), urlItem('u1', 'https://example.com/menu')];
    render(<CanvasContent items={items} workflowId="wf-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Library, 3 items' }));
    const library = screen.getByRole('region', { name: 'Library' });
    expect(library).toHaveTextContent('Artifacts · 1');
    expect(library).toHaveTextContent('Files · 2');
    expect(within(library).getByRole('button', { name: /Weekly plan/ })).toHaveTextContent('Note · v1');
    expect(within(library).getByRole('button', { name: /booking\.png/ })).toHaveTextContent('Image · 2.0 KB');
    expect(within(library).getByRole('button', { name: /example\.com\/menu/ })).toHaveTextContent('Web page · example.com');

    fireEvent.click(within(library).getByRole('button', { name: /Weekly plan/ }));
    expect(screen.queryByRole('region', { name: 'Library' })).not.toBeInTheDocument();
    expect(screen.getByText('the plan')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Latest' })).toBeInTheDocument();
  });

  // Colour marks state or role (docs-internal/theme_system.md): a kind's
  // tile is neutral, as a reply's document card's is, and the item on show
  // is marked by a foreground border.
  it('draws every kind’s tile neutral, and marks the item on show in the foreground colour', () => {
    const items = [titled(note('n1', 'the plan'), 'Weekly plan'), image('i1', 'booking.png'), urlItem('u1', 'https://example.com/menu')];
    const { container } = render(<CanvasContent items={items} workflowId="wf-1" />);

    expect(container.innerHTML).not.toMatch(/action-(tools|run|stop|save|config)/);
    expect(screen.getByRole('button', { name: /example\.com\/menu/ })).toHaveClass('aria-current:border-fg-default', 'aria-current:bg-bg-active');

    fireEvent.click(screen.getByRole('button', { name: 'Library, 3 items' }));
    const library = screen.getByRole('region', { name: 'Library' });
    expect(library.innerHTML).not.toMatch(/action-(tools|run|stop|save|config)/);
    for (const tile of library.querySelectorAll('span[aria-hidden]')) {
      expect(tile).toHaveClass('border-border-default', 'bg-bg-active', 'text-fg-muted');
    }
    expect(within(library).getByRole('button', { name: /Weekly plan/ })).toHaveClass('aria-current:border-fg-default');
  });

  it('goes back from the Library to the item that was on show', () => {
    const items = [titled(note('n1', 'the plan'), 'Weekly plan'), titled(note('n2', 'the menu'), 'Menu')];
    render(<CanvasContent items={items} workflowId="wf-1" />);
    fireEvent.click(screen.getByRole('button', { name: 'Library, 2 items' }));
    // The Library takes the item's place, and marks the one that was on show.
    const library = screen.getByRole('region', { name: 'Library' });
    expect(screen.queryByText('the menu')).not.toBeInTheDocument();
    expect(within(library).getByRole('button', { name: /Menu/ })).toHaveAttribute('aria-current', 'true');
    expect(within(library).getByRole('button', { name: /Weekly plan/ })).not.toHaveAttribute('aria-current');

    fireEvent.click(within(library).getByRole('button', { name: 'Back' }));
    expect(screen.queryByRole('region', { name: 'Library' })).not.toBeInTheDocument();
    expect(screen.getByText('the menu')).toBeInTheDocument();
    // Still following the newest.
    expect(screen.queryByRole('button', { name: 'Latest' })).not.toBeInTheDocument();
  });
});
