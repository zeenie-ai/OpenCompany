/**
 * The global hover-sound delegate (useSoundSync): entering one of the
 * listed surfaces plays the hover sound once. Command list items are among
 * them; cmdk marks them with a `cmdk-item` attribute, not a class. A raw
 * cmdk item is rendered here, as the editor's command palette uses one (the
 * shadcn CommandItem also carries `menu-pop-item`).
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { Command } from 'cmdk';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { Sounds } from '@/lib/sound';
import { useSoundSync } from '../useSound';

beforeAll(() => {
  // jsdom has no scrollIntoView; cmdk scrolls its selected item into view.
  if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => undefined;
});

function SoundHost({ children }: { children: ReactNode }) {
  useSoundSync();
  return <>{children}</>;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('the hover-sound delegate', () => {
  it('plays on entering a command palette row, and not on plain text', () => {
    const play = vi.spyOn(Sounds, 'play').mockImplementation(() => {});
    render(
      <ThemeProvider>
        <SoundHost>
          <Command>
            <Command.List>
              <Command.Item>Open settings</Command.Item>
            </Command.List>
          </Command>
          <p>Plain text</p>
        </SoundHost>
      </ThemeProvider>,
    );

    fireEvent.mouseOver(screen.getByText('Open settings'));
    expect(play).toHaveBeenCalledWith('hover');

    play.mockClear();
    fireEvent.mouseOver(screen.getByText('Plain text'));
    expect(play).not.toHaveBeenCalled();
  });
});
