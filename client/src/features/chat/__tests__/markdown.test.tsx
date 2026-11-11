/**
 * Markdown in chat replies: images from outside the workspace wait for the
 * owner (a reply could otherwise leak data through an image URL the moment
 * it renders), workspace images load, and links open in a new tab. A reply's
 * code block is framed with its language, Copy and line numbers, and its
 * table is framed too.
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ReactMarkdown from 'react-markdown';

import { CHAT_MARKDOWN_COMPONENTS, isWorkspaceImage } from '..';
import ReplyMarkdown from '../markdown/ReplyMarkdown';

function renderReply(text: string) {
  return render(<ReactMarkdown components={CHAT_MARKDOWN_COMPONENTS}>{text}</ReactMarkdown>);
}

describe('isWorkspaceImage', () => {
  const origin = window.location.origin;

  it.each([
    ['/api/workspace/wf-1/files/shot.png', true],
    [`${origin}/api/workspace/wf-1/files/shot.png`, true],
    ['https://elsewhere.example/api/workspace/wf-1/files/shot.png', false],
    ['https://tracker.example/pixel.png?d=secret', false],
    ['/api/other/thing.png', false],
    ['', false],
    [undefined, false],
  ])('%j -> %s', (src, expected) => {
    expect(isWorkspaceImage(src)).toBe(expected);
  });
});

describe('chat markdown', () => {
  it('loads a workspace image straight away', () => {
    renderReply('![chart](/api/workspace/wf-1/files/chart.png)');
    const img = screen.getByRole('img', { name: 'chart' });
    expect(img).toHaveAttribute('src', '/api/workspace/wf-1/files/chart.png');
    expect(img).toHaveAttribute('referrerpolicy', 'no-referrer');
  });

  it('holds a remote image until the owner asks for it', async () => {
    const user = userEvent.setup();
    renderReply('![x](https://tracker.example/pixel.png?d=secret)');

    expect(screen.queryByRole('img')).toBeNull();
    const reveal = screen.getByRole('button', { name: 'Show image from tracker.example' });

    await user.click(reveal);

    const img = screen.getByRole('img', { name: 'x' });
    expect(img).toHaveAttribute('src', 'https://tracker.example/pixel.png?d=secret');
    expect(img).toHaveAttribute('referrerpolicy', 'no-referrer');
  });

  it('loads an image named by its workspace path from the chat’s workflow', () => {
    render(<ReplyMarkdown text={'![chart](charts/revenue.png) and [the data](data/q2.csv)'} workflowId="wf-1" />);

    expect(screen.getByRole('img', { name: 'chart' })).toHaveAttribute('src', '/api/workspace/wf-1/files/charts/revenue.png');
    expect(screen.getByRole('link', { name: 'the data' })).toHaveAttribute('href', '/api/workspace/wf-1/files/data/q2.csv');
  });

  it('opens links in a new tab without an opener', () => {
    renderReply('[docs](https://example.com/docs)');
    const link = screen.getByRole('link', { name: 'docs' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });
});

describe('reply code blocks and tables', () => {
  it('frames a code block with its language, line numbers and Copy', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const { container } = render(<ReplyMarkdown text={'Here it is:\n\n```json\n{\n  "ok": true\n}\n```'} />);

    const frame = container.querySelector<HTMLElement>('figure.chat-code');
    expect(frame).not.toBeNull();
    expect(within(frame!).getByText('json').closest('figcaption')).not.toBeNull();
    const gutter = frame!.querySelector('pre')?.previousElementSibling;
    expect(gutter).toHaveAttribute('aria-hidden', 'true');
    expect(Array.from(gutter?.children ?? [], (line) => line.textContent)).toEqual(['1', '2', '3']);

    fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));
    expect(writeText).toHaveBeenCalledWith('{\n  "ok": true\n}');
    expect(await screen.findByRole('button', { name: 'Copied' })).toHaveTextContent('Copied');
  });

  it('labels a block without a language "text", and leaves inline code unframed', () => {
    const { container } = render(<ReplyMarkdown text={'Run `ls` first:\n\n```\nls -la\n```'} />);
    expect(container.querySelectorAll('figure.chat-code')).toHaveLength(1);
    expect(within(container.querySelector<HTMLElement>('figcaption')!).getByText('text')).toBeInTheDocument();
    expect(screen.getByText('ls').closest('figure')).toBeNull();
  });

  it('frames a table', () => {
    render(<ReplyMarkdown text={'| Day | Slot |\n| --- | --- |\n| Sat | 10:00 |'} />);
    expect(screen.getByRole('table').parentElement).toHaveClass('chat-table');
  });
});
