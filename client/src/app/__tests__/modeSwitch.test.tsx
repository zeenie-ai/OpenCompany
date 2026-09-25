/**
 * The Normal / Dev switch end to end: the real toggle, shell actions,
 * transition, screen switch and theme rule. Only the two screens are
 * stand-ins.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../lib/featureFlags', () => ({ featureFlags: { normalMode: true, nodeSpecBackend: true } }));
vi.mock('../../Dashboard', () => ({ default: () => <div>Workflow editor</div> }));
vi.mock('../../features/home/HomeShell', () => ({ default: () => <div>Home screen</div> }));

import { ModeToggle } from '../../components/shell/ModeToggle';
import { useAppStore } from '../../store/useAppStore';
import { ShellModeSwitch } from '../ShellModeSwitch';
import { ShellThemeProvider } from '../ShellThemeProvider';

const html = document.documentElement;

beforeEach(() => {
  localStorage.clear();
  useAppStore.setState({ shellMode: 'dev', hasUnsavedChanges: false });
});

afterEach(() => {
  html.removeAttribute('data-theme');
  html.className = '';
});

describe('mode switch', () => {
  it('moves from the editor to Home and back', async () => {
    const user = userEvent.setup();
    render(
      <>
        <ModeToggle />
        <ShellModeSwitch />
      </>,
    );
    expect(await screen.findByText('Workflow editor')).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: 'Normal' }));
    expect(await screen.findByText('Home screen')).toBeInTheDocument();
    expect(screen.queryByText('Workflow editor')).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Normal' })).toHaveAttribute('aria-checked', 'true');
    expect(localStorage.getItem('ui_shell_mode')).toBe('normal');

    await user.click(screen.getByRole('radio', { name: 'Dev' }));
    expect(await screen.findByText('Workflow editor')).toBeInTheDocument();
    expect(screen.queryByText('Home screen')).not.toBeInTheDocument();
    expect(localStorage.getItem('ui_shell_mode')).toBe('dev');
  });

  it('shows Home in the base theme and the editor in the chosen one', async () => {
    localStorage.setItem('opencompany-theme', 'atomic');
    const user = userEvent.setup();
    render(
      <ShellThemeProvider>
        <ModeToggle />
        <ShellModeSwitch />
      </ShellThemeProvider>,
    );
    await screen.findByText('Workflow editor');
    expect(html.dataset.theme).toBe('atomic');

    await user.click(screen.getByRole('radio', { name: 'Normal' }));
    await screen.findByText('Home screen');
    expect(html.dataset.theme).toBe('light');
    expect(localStorage.getItem('opencompany-theme')).toBe('atomic');

    await user.click(screen.getByRole('radio', { name: 'Dev' }));
    await screen.findByText('Workflow editor');
    expect(html.dataset.theme).toBe('atomic');
  });

  it('ignores a click on the mode already showing', async () => {
    const user = userEvent.setup();
    render(
      <>
        <ModeToggle />
        <ShellModeSwitch />
      </>,
    );
    await screen.findByText('Workflow editor');
    await user.click(screen.getByRole('radio', { name: 'Dev' }));
    expect(screen.getByText('Workflow editor')).toBeInTheDocument();
    expect(useAppStore.getState().shellMode).toBe('dev');
  });
});
