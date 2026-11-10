/**
 * The Workspace dock's shell and its small parts, the same on Home and in
 * the editor: a panel that opens and closes by its width, out of reach while
 * closed, whose contents mount on the first open and stay; a neutral left
 * edge that drags; lying over the page in a narrow window; Expand and Close;
 * and Take over.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { useEffect } from 'react';
import { DUR_FALLBACK } from '@/lib/motion';
import { installWaapiStub } from '@/test/waapi';
import { TakeOverButton } from '../TakeOverButton';
import { DockButtons, WorkspaceDockShell } from '../WorkspaceDockShell';

/** The shell around one line of contents, and what it tells its host. The
 *  width is plain pixels: jsdom garbles the min() and max() the hosts pass. */
function setup(open: boolean) {
  const onWidth = vi.fn();
  const onResizing = vi.fn();
  const mounted = vi.fn();
  function Contents() {
    useEffect(() => {
      mounted();
    }, []);
    return <p>Inside</p>;
  }
  const shell = (isOpen: boolean) => (
    <WorkspaceDockShell open={isOpen} width="480px" onWidth={onWidth} onResizing={onResizing}>
      <Contents />
    </WorkspaceDockShell>
  );
  const view = render(shell(open));
  return { onWidth, onResizing, mounted, show: (isOpen: boolean) => view.rerender(shell(isOpen)) };
}

/** The box the contents sit in, laid out at the dock's full width. */
const body = () => screen.getByText('Inside').parentElement;
const edge = () => screen.getByRole('separator', { name: 'Resize workspace' });

let waapi: ReturnType<typeof installWaapiStub>;

beforeEach(() => {
  waapi = installWaapiStub();
});

afterEach(() => waapi.restore());

describe('WorkspaceDockShell', () => {
  it('is out of reach while closed, with nothing mounted before the first open', () => {
    const { mounted } = setup(false);
    // A hidden element has no accessible name, so it is found by role alone.
    const dock = screen.getByRole('complementary', { hidden: true });
    expect(dock).toHaveAttribute('aria-label', 'Workspace');
    expect(dock).toHaveAttribute('aria-hidden', 'true');
    expect(dock).toHaveAttribute('inert');
    expect(dock.style.width).toBe('0px');
    expect(screen.queryByText('Inside')).toBeNull();
    expect(mounted).not.toHaveBeenCalled();
  });

  it('opens to the width it is given and mounts its contents', () => {
    const { show, mounted } = setup(false);
    show(true);
    const dock = screen.getByRole('complementary', { name: 'Workspace' });
    expect(dock).not.toHaveAttribute('inert');
    expect(dock.style.width).toBe('480px');
    // The contents take the full width whatever the dock's own is, so nothing reflows as it opens.
    expect(body()?.style.width).toBe('480px');
    expect(mounted).toHaveBeenCalledOnce();
  });

  it('keeps its contents mounted through a close', () => {
    const { show, mounted } = setup(true);
    const inside = screen.getByText('Inside');
    show(false);
    const dock = screen.getByRole('complementary', { hidden: true });
    expect(dock).toHaveAttribute('aria-hidden', 'true');
    expect(dock).toHaveAttribute('inert');
    expect(dock.style.width).toBe('0px');
    // Still there at the full width, clipped by the closed dock.
    expect(screen.getByText('Inside')).toBe(inside);
    expect(body()?.style.width).toBe('480px');
    show(true);
    expect(screen.getByText('Inside')).toBe(inside);
    expect(mounted).toHaveBeenCalledOnce();
  });

  it('slides its contents in each time it opens, the first time included', () => {
    const { show } = setup(false);
    expect(waapi.calls).toHaveLength(0);
    show(true);
    expect(waapi.calls).toHaveLength(1);
    expect(waapi.calls[0].target).toBe(body());
    expect(waapi.calls[0].options).toMatchObject({ duration: DUR_FALLBACK['dock-in'] });
    // Closing only narrows it.
    show(false);
    expect(waapi.calls).toHaveLength(1);
    show(true);
    expect(waapi.calls).toHaveLength(2);
    expect(waapi.calls[1].target).toBe(body());
  });

  it('does not slide in when it starts open, as after a reload', () => {
    setup(true);
    expect(screen.getByText('Inside')).toBeInTheDocument();
    expect(waapi.calls).toHaveLength(0);
  });

  it('has a neutral edge to drag, never a role or state colour', () => {
    setup(true);
    const handle = edge();
    expect(handle).toHaveAttribute('aria-orientation', 'vertical');
    expect(handle).toHaveClass('cursor-ew-resize', 'hover:bg-border-strong');
    expect(handle.className).not.toMatch(/-(node|action)-/);
    // The same grey while it is dragged.
    fireEvent.mouseDown(handle, { clientX: 900 });
    expect(handle).toHaveClass('bg-border-strong');
    expect(handle.className).not.toMatch(/-(node|action)-/);
    fireEvent.mouseUp(document);
    expect(handle).not.toHaveClass('bg-border-strong');
  });

  it('asks for the width it started at less how far the mouse went, and says while it is dragged', () => {
    const { onWidth, onResizing } = setup(true);
    const dock = screen.getByRole('complementary', { name: 'Workspace' });
    // jsdom lays nothing out, so say how wide the dock is on screen.
    vi.spyOn(dock, 'getBoundingClientRect').mockReturnValue({ width: 500 } as DOMRect);
    // What it said when it mounted is not the drag's.
    onResizing.mockClear();

    fireEvent.mouseDown(edge(), { clientX: 900 });
    expect(onResizing.mock.calls).toEqual([[true]]);
    // The width follows the mouse at once, and the contents take no mouse moves from it.
    expect(dock).toHaveClass('transition-none');
    expect(body()).toHaveClass('pointer-events-none');
    // A press alone asks for no width.
    expect(onWidth).not.toHaveBeenCalled();

    // The edge is the left one: left widens, right narrows, each from where the drag began.
    fireEvent.mouseMove(document, { clientX: 860 });
    expect(onWidth).toHaveBeenLastCalledWith(540);
    fireEvent.mouseMove(document, { clientX: 950 });
    expect(onWidth).toHaveBeenLastCalledWith(450);

    fireEvent.mouseUp(document);
    expect(onResizing.mock.calls).toEqual([[true], [false]]);
    expect(dock).not.toHaveClass('transition-none');
    expect(body()).not.toHaveClass('pointer-events-none');
    // The drag is over: the mouse moves nothing now.
    fireEvent.mouseMove(document, { clientX: 700 });
    expect(onWidth).toHaveBeenCalledTimes(2);
  });

  it('drags with no one listening for it, as in the editor', () => {
    const onWidth = vi.fn();
    render(
      <WorkspaceDockShell open width="480px" onWidth={onWidth}>
        <p>Inside</p>
      </WorkspaceDockShell>,
    );
    fireEvent.mouseDown(edge(), { clientX: 900 });
    fireEvent.mouseMove(document, { clientX: 880 });
    fireEvent.mouseUp(document);
    expect(onWidth).toHaveBeenCalledOnce();
  });

  it('lies over the page in a window under 1100px, with a modal’s shadow only while open', () => {
    const { show } = setup(true);
    const dock = screen.getByRole('complementary', { name: 'Workspace' });
    // Wider than that it is in the page's flow, and pushes the page aside.
    const placed = ['relative', 'shrink-0', 'max-[1100px]:absolute', 'max-[1100px]:inset-y-0', 'max-[1100px]:right-0', 'max-[1100px]:z-30'];
    expect(dock).toHaveClass(...placed, 'max-[1100px]:shadow-modal');
    show(false);
    expect(dock).toHaveClass(...placed);
    expect(dock).not.toHaveClass('max-[1100px]:shadow-modal');
  });
});

describe('DockButtons', () => {
  it('offers a round quiet Expand, then Close workspace', () => {
    const onToggleWide = vi.fn();
    const onClose = vi.fn();
    render(<DockButtons wide={false} onToggleWide={onToggleWide} onClose={onClose} />);
    const expand = screen.getByRole('button', { name: 'Expand' });
    const close = screen.getByRole('button', { name: 'Close workspace' });
    expect(screen.getAllByRole('button')).toEqual([expand, close]);
    expect(expand).toHaveAttribute('title', 'Expand');
    expect(close).toHaveAttribute('title', 'Close workspace');
    for (const button of [expand, close]) {
      expect(button).toHaveAttribute('data-variant', 'quiet');
      expect(button).toHaveClass('size-7.5', 'rounded-full');
    }

    fireEvent.click(expand);
    expect(onToggleWide).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledOnce();
    expect(onToggleWide).toHaveBeenCalledOnce();
  });

  it('offers Restore size in place of Expand while the dock is expanded', () => {
    const onToggleWide = vi.fn();
    render(<DockButtons wide onToggleWide={onToggleWide} onClose={vi.fn()} />);
    const restore = screen.getByRole('button', { name: 'Restore size' });
    expect(screen.queryByRole('button', { name: 'Expand' })).toBeNull();
    expect(restore).toHaveAttribute('title', 'Restore size');
    expect(restore).toHaveAttribute('data-variant', 'quiet');
    expect(restore).toHaveClass('size-7.5', 'rounded-full');
    fireEvent.click(restore);
    expect(onToggleWide).toHaveBeenCalledOnce();
  });
});

describe('TakeOverButton', () => {
  it('is a neutral pill that says Take over', () => {
    render(<TakeOverButton />);
    const button = screen.getByRole('button', { name: 'Take over' });
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute('data-variant', 'quiet');
    expect(button).toHaveClass(
      'h-8.5', 'shrink-0', 'rounded-pill', 'border-border-default', 'bg-bg-elevated',
      'px-3.5', 'text-sm', 'font-medium', 'text-fg-default',
    );
    // Colour marks a state or a role, and this is neither.
    expect(button.className).not.toMatch(/-(node|action)-/);
  });

  it('passes its children, a click and disabled through', () => {
    const onClick = vi.fn();
    const { rerender } = render(<TakeOverButton onClick={onClick}>Hand back</TakeOverButton>);
    const button = screen.getByRole('button', { name: 'Hand back' });
    expect(screen.queryByRole('button', { name: 'Take over' })).toBeNull();
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
    rerender(<TakeOverButton onClick={onClick} disabled>Hand back</TakeOverButton>);
    expect(button).toBeDisabled();
  });

  it('takes a caller’s classes over its own', () => {
    render(<TakeOverButton className="ml-1 h-7.5" />);
    const button = screen.getByRole('button', { name: 'Take over' });
    expect(button).toHaveClass('ml-1', 'h-7.5', 'rounded-pill', 'bg-bg-elevated');
    expect(button).not.toHaveClass('h-8.5');
  });
});
