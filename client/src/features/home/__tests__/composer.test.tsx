/**
 * The composer's keys: Enter sends, Shift+Enter is a new line, Escape stops
 * editing, and an Enter that confirms an IME composition never sends.
 */

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { Composer, type ComposerProps } from '../hire/Composer';
import { createLabel, isSendKey } from '../hire/composerKeys';
import { greetingFor } from '../hire/greeting';

function setup(overrides: Partial<ComposerProps> = {}) {
  const props: ComposerProps = {
    value: 'Answer my WhatsApp',
    onChange: vi.fn(),
    onSubmit: vi.fn(),
    refining: false,
    onStopRefining: vi.fn(),
    working: false,
    apps: [],
    onOpenApps: vi.fn(),
    ...overrides,
  };
  render(
    <ThemeProvider>
      <Composer {...props} />
    </ThemeProvider>,
  );
  return { props, box: screen.getByRole('textbox') };
}

describe('isSendKey', () => {
  it('sends on a plain Enter only', () => {
    expect(isSendKey({ key: 'Enter', shiftKey: false })).toBe(true);
    expect(isSendKey({ key: 'Enter', shiftKey: true })).toBe(false);
    expect(isSendKey({ key: 'a', shiftKey: false })).toBe(false);
  });

  it('never sends the Enter that confirms an IME composition', () => {
    expect(isSendKey({ key: 'Enter', shiftKey: false, nativeEvent: { isComposing: true } })).toBe(false);
    expect(isSendKey({ key: 'Enter', shiftKey: false, keyCode: 229 })).toBe(false);
    expect(isSendKey({ key: 'Enter', shiftKey: false, nativeEvent: { keyCode: 229 } })).toBe(false);
  });
});

describe('Composer', () => {
  it('sends with Enter and keeps Shift+Enter for a new line', () => {
    const { props, box } = setup();
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    expect(props.onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(props.onSubmit).toHaveBeenCalledTimes(1);
  });

  it('does not send while composing', () => {
    const { props, box } = setup();
    fireEvent.keyDown(box, { key: 'Enter', isComposing: true });
    fireEvent.keyDown(box, { key: 'Enter', keyCode: 229 });
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it('stops editing the draft with Escape', () => {
    const { props, box } = setup({ refining: true });
    expect(screen.getByText('Editing the draft')).toBeInTheDocument();
    expect(box).toHaveAttribute('placeholder', 'What should change? e.g. Only reply during business hours');
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(props.onStopRefining).toHaveBeenCalledTimes(1);
  });

  it('cannot create with nothing written or while a setup is being written', () => {
    setup({ value: '   ' });
    expect(screen.getByRole('button', { name: /Create employee/ })).toBeDisabled();
  });

  it('labels Create by what it will do', () => {
    expect(createLabel(true, false)).toBe('Creating…');
    expect(createLabel(false, true)).toBe('Update');
    expect(createLabel(false, false)).toBe('Create employee');
  });

  it('counts the connected apps and opens Connectors', () => {
    const { props } = setup({ apps: [{ id: 'whatsapp', name: 'WhatsApp' }] });
    fireEvent.click(screen.getByRole('button', { name: /1 app/ }));
    expect(props.onOpenApps).toHaveBeenCalledTimes(1);
  });
});

describe('greetingFor', () => {
  it.each([
    [3, 'Late night'],
    [9, 'Good morning'],
    [14, 'Good afternoon'],
    [20, 'Good evening'],
  ])('%i o’clock is %s', (hour, greeting) => {
    expect(greetingFor(new Date(2026, 8, 25, hour))).toBe(greeting);
  });
});
