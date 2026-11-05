import { useEffect } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { FullView } from '../FullView';

afterEach(() => {
  cleanup(); vi.restoreAllMocks();
  Reflect.deleteProperty(document, 'fullscreenElement');
  Reflect.deleteProperty(document, 'exitFullscreen');
  Reflect.deleteProperty(HTMLElement.prototype, 'requestFullscreen');
});

it('enters and exits full view without remounting the viewer', async () => {
  let fullscreen: Element | null = null;
  const mount = vi.fn();
  function Viewer() { useEffect(() => { mount(); }, []); return <canvas data-testid="viewer" />; }
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => fullscreen });
  const request = vi.fn(() => {
    fullscreen = screen.getByTestId('viewer').parentElement;
    document.dispatchEvent(new Event('fullscreenchange')); return Promise.resolve();
  });
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: request });
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: vi.fn(async () => {
    fullscreen = null; document.dispatchEvent(new Event('fullscreenchange'));
  }) });
  render(<FullView label="Phone"><Viewer /></FullView>);
  const canvas = screen.getByTestId('viewer');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Phone full view' })); });
  expect(screen.getByRole('button', { name: 'Exit phone full view' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByTestId('viewer')).toBe(canvas);
  // Escape is owned by the browser; fullscreenchange synchronizes the UI.
  await act(async () => { await document.exitFullscreen(); });
  expect(screen.getByRole('button', { name: 'Phone full view' })).toHaveFocus();
  expect(mount).toHaveBeenCalledTimes(1);
});

it('explains when fullscreen is unavailable without hiding the viewer', async () => {
  render(<FullView label="Browser"><canvas data-testid="viewer" /></FullView>);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Browser full view' })); });
  expect(screen.getByRole('alert')).toHaveTextContent('Full view is unavailable');
  expect(screen.getByTestId('viewer')).toBeInTheDocument();
});

it('collapses secondary controls without losing their state or remounting the viewer', () => {
  const mount = vi.fn();
  function Viewer() { useEffect(() => { mount(); }, []); return <canvas data-testid="viewer" />; }
  render(<FullView label="Browser" toolbar={<span role="status">Live browser</span>} controls={<input aria-label="Address" defaultValue="example.com" />}><Viewer /></FullView>);
  const toggle = screen.getByRole('button', { name: 'Browser controls' });
  const viewer = screen.getByTestId('viewer');
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('textbox', { name: 'Address' })).toBeNull();
  fireEvent.click(toggle);
  fireEvent.change(screen.getByRole('textbox', { name: 'Address' }), { target: { value: 'example.org' } });
  fireEvent.click(toggle);
  expect(screen.getByRole('status')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Browser full view' })).toBeVisible();
  fireEvent.click(toggle);
  expect(screen.getByRole('textbox', { name: 'Address' })).toHaveValue('example.org');
  expect(screen.getByTestId('viewer')).toBe(viewer);
  expect(mount).toHaveBeenCalledTimes(1);
});

it('draws only the viewer in Home, whose dock has its own Expand', () => {
  render(<FullView label="Browser" chrome="home" toolbar={<span role="status">Live browser</span>} controls={<input aria-label="Address" />}><canvas data-testid="viewer" /></FullView>);
  const root = screen.getByTestId('viewer').parentElement;
  expect(root?.children).toHaveLength(1);
  // The root the viewers' group-data selectors read is kept.
  expect(root).toHaveAttribute('data-full-view', 'false');
  expect(root).toHaveClass('group/fullview');
  expect(screen.queryByRole('button', { hidden: true })).toBeNull();
  expect(screen.queryByRole('status', { hidden: true })).toBeNull();
  expect(screen.queryByRole('textbox', { hidden: true })).toBeNull();
});
