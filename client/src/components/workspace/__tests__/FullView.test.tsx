import { useEffect } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { FullView, FullViewButton } from '../FullView';

afterEach(() => {
  cleanup(); vi.restoreAllMocks();
  Reflect.deleteProperty(document, 'fullscreenElement');
  Reflect.deleteProperty(document, 'exitFullscreen');
  Reflect.deleteProperty(HTMLElement.prototype, 'requestFullscreen');
});

/** A browser that can fill the screen: the element asked is the one that
 *  fills it, and `fullscreenchange` tells the page. */
function fullscreenApi() {
  let fullscreen: Element | null = null;
  const fill = (element: Element | null) => { fullscreen = element; document.dispatchEvent(new Event('fullscreenchange')); };
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => fullscreen });
  const request = vi.fn(function (this: HTMLElement) { fill(this); return Promise.resolve(); });
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: request });
  const exit = vi.fn(async () => { fill(null); });
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: exit });
  return { request, exit };
}

it('enters and exits full view without remounting the viewer', async () => {
  fullscreenApi();
  const mount = vi.fn();
  function Viewer() { useEffect(() => { mount(); }, []); return <canvas data-testid="viewer" />; }
  render(<FullView><FullViewButton label="Phone" /><Viewer /></FullView>);
  const canvas = screen.getByTestId('viewer');
  expect(screen.getByRole('button', { name: 'Phone full view' })).toHaveAttribute('aria-pressed', 'false');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Phone full view' })); });
  const leave = screen.getByRole('button', { name: 'Exit phone full view' });
  expect(leave).toHaveAttribute('aria-pressed', 'true');
  expect(leave).toHaveAttribute('title', 'Exit full view');
  // The box the view sits in is what fills the screen.
  expect(document.fullscreenElement).toBe(canvas.parentElement);
  expect(canvas.parentElement).toHaveAttribute('data-full-view', 'true');
  expect(screen.getByTestId('viewer')).toBe(canvas);
  // Escape is owned by the browser; fullscreenchange synchronizes the UI.
  await act(async () => { await document.exitFullscreen(); });
  expect(screen.getByRole('button', { name: 'Phone full view' })).toHaveFocus();
  expect(canvas.parentElement).toHaveAttribute('data-full-view', 'false');
  expect(mount).toHaveBeenCalledTimes(1);
});

it('leaves full view from the same button', async () => {
  const { request, exit } = fullscreenApi();
  render(<FullView><FullViewButton label="Browser" /><canvas data-testid="viewer" /></FullView>);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Browser full view' })); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Exit browser full view' })); });
  expect(exit).toHaveBeenCalledOnce();
  expect(request).toHaveBeenCalledOnce();
  expect(screen.getByRole('button', { name: 'Browser full view' })).toHaveAttribute('aria-pressed', 'false');
});

it('explains when fullscreen is unavailable without hiding the viewer', async () => {
  render(<FullView><FullViewButton label="Browser" /><canvas data-testid="viewer" /></FullView>);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Browser full view' })); });
  expect(screen.getByRole('alert')).toHaveTextContent('Full view is unavailable');
  expect(screen.getByTestId('viewer')).toBeInTheDocument();
});

it('says so when the browser refuses full view, until the next try', async () => {
  const request = vi.fn<() => Promise<void>>(() => Promise.reject(new Error('denied')));
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: request });
  render(<FullView><FullViewButton label="Browser" /><canvas data-testid="viewer" /></FullView>);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Browser full view' })); });
  expect(screen.getByRole('alert')).toHaveTextContent('Could not open full view');
  // A try that works leaves no old message behind.
  request.mockImplementation(() => Promise.resolve());
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Browser full view' })); });
  expect(screen.queryByRole('alert')).toBeNull();
});

it('draws only the view by itself: the button is the view’s to place', () => {
  render(<FullView><canvas data-testid="viewer" /></FullView>);
  const root = screen.getByTestId('viewer').parentElement;
  expect(root?.children).toHaveLength(1);
  // The root the views' group-data selectors read.
  expect(root).toHaveAttribute('data-full-view', 'false');
  expect(root).toHaveClass('group/fullview');
  expect(screen.queryByRole('button', { hidden: true })).toBeNull();
});

it('is a round quiet button like the ones beside it', () => {
  render(<FullView><FullViewButton label="Browser" /></FullView>);
  const button = screen.getByRole('button', { name: 'Browser full view' });
  expect(button).toHaveAttribute('data-variant', 'quiet');
  expect(button).toHaveAttribute('title', 'Full view');
  expect(button).toHaveClass('size-7.5', 'rounded-full');
});

it('takes its host’s classes over its own', () => {
  // The phone's buttons are squarer, with larger icons.
  render(<FullView><FullViewButton label="Phone" className="rounded-lg [&_svg]:size-4" /></FullView>);
  const button = screen.getByRole('button', { name: 'Phone full view' });
  expect(button).toHaveClass('size-7.5', 'rounded-lg', '[&_svg]:size-4');
  expect(button).not.toHaveClass('rounded-full');
});

it('draws nothing outside a full view box', () => {
  const { container } = render(<FullViewButton label="Browser" />);
  expect(container).toBeEmptyDOMElement();
});
