/**
 * The theme button, the same in both modes and on the sign-in screen: each
 * press switches to the other theme, and its name says which one that is.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { setReducedMotion } from '@/test/waapi';
import { ThemeButton } from '../ThemeButton';

const html = document.documentElement;
let restoreMotion: () => void;

beforeEach(() => {
  localStorage.clear();
  restoreMotion = setReducedMotion(true);
});

afterEach(() => {
  restoreMotion();
  localStorage.clear();
  html.removeAttribute('data-theme');
  html.className = '';
});

describe('ThemeButton', () => {
  it('switches from Dark to Light and back to Dark', async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ThemeButton />
      </ThemeProvider>,
    );

    // A first launch opens in Dark.
    await user.click(screen.getByRole('button', { name: 'Switch to Light' }));
    expect(html.dataset.theme).toBe('light');
    expect(html.classList.contains('dark')).toBe(false);

    await user.click(screen.getByRole('button', { name: 'Switch to Dark' }));
    expect(html.dataset.theme).toBe('dark');
    expect(html.classList.contains('dark')).toBe(true);
    expect(localStorage.getItem('opencompany-theme')).toBe('dark');
  });
});
