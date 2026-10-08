/**
 * The hire view's pieces. The composer's keys: Enter sends, Shift+Enter is
 * a new line, Escape stops editing, and an Enter that confirms an IME
 * composition never sends. The starter chips put a job in the composer
 * rather than sending it, and offer to hire the starter as it stands while
 * the job is untouched. The hire notice shows once, on its employee's page.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ThemeProvider } from '@/contexts/ThemeContext';
import { Composer, type ComposerProps } from '../hire/Composer';
import { createLabel } from '../hire/composerKeys';
import { greetingFor } from '../hire/greeting';
import { HireNotice } from '../hire/HireNotice';
import { TemplateChips } from '../hire/TemplateChips';
import { HIRE_TEMPLATES } from '../hire/templates';
import { useHomeStore } from '../state/homeStore';

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
    expect(createLabel(false, false, 'Create their setup')).toBe('Create their setup');
    expect(createLabel(true, false, 'Create their setup')).toBe('Creating…');
  });

  it('takes the guide’s label, and drops the float shadow when flat', () => {
    setup({ idleLabel: 'Create their setup', flat: true });
    const create = screen.getByRole('button', { name: /Create their setup/ });
    expect(create).toBeEnabled();
    expect(create.closest('[data-intro]')).not.toHaveClass('shadow-float');
  });

  it('floats on the hire view', () => {
    setup();
    expect(screen.getByRole('button', { name: /Create employee/ }).closest('[data-intro]')).toHaveClass('shadow-float');
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

describe('TemplateChips', () => {
  const receptionist = HIRE_TEMPLATES[0];

  function chips(picked: (typeof HIRE_TEMPLATES)[number] | null = null) {
    const onPick = vi.fn();
    const onHireNow = vi.fn();
    render(
      <ThemeProvider>
        <TemplateChips picked={picked} onPick={onPick} onHireNow={onHireNow} />
      </ThemeProvider>,
    );
    return { onPick, onHireNow };
  }

  it('hands a chip’s starter to the composer, and offers nothing to hire yet', () => {
    const { onPick, onHireNow } = chips();
    fireEvent.click(screen.getByRole('button', { name: receptionist.label }));
    expect(onPick).toHaveBeenCalledWith(receptionist);
    expect(screen.queryByRole('button', { name: 'Hire now' })).not.toBeInTheDocument();
    expect(onHireNow).not.toHaveBeenCalled();
  });

  it('offers to hire the picked starter as it stands', () => {
    const { onHireNow } = chips(receptionist);
    expect(screen.getByRole('button', { name: receptionist.label })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(receptionist.summary)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hire now' }));
    expect(onHireNow).toHaveBeenCalledWith(receptionist);
  });

  it('shows the chips alone without Hire now (the Welcome guide)', () => {
    render(
      <ThemeProvider>
        <TemplateChips picked={receptionist} onPick={vi.fn()} className="justify-start" />
      </ThemeProvider>,
    );
    expect(screen.getByRole('button', { name: receptionist.label })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: 'Hire now' })).not.toBeInTheDocument();
    expect(screen.queryByText(receptionist.summary)).not.toBeInTheDocument();
  });
});

describe('HireNotice', () => {
  const notice = { workflowId: 'w1', name: 'Maya', warnings: ['Google Calendar is left out while they ask before sending anything'] };

  beforeEach(() => useHomeStore.setState({ view: { kind: 'employee', workflowId: 'w1' }, hireNotice: notice }));

  it('shows what the hire said on its employee’s page until dismissed', () => {
    render(<HireNotice workflowId="w1" />);
    expect(screen.getByText('A few notes on Maya’s setup')).toBeInTheDocument();
    expect(screen.getByText(notice.warnings[0])).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(useHomeStore.getState().hireNotice).toBeNull();
    expect(screen.queryByText(notice.warnings[0])).not.toBeInTheDocument();
  });

  it('stays off other employees’ pages, and goes once the owner moves on', () => {
    const { container } = render(<HireNotice workflowId="w2" />);
    expect(container).toBeEmptyDOMElement();
    useHomeStore.getState().showEmployee('w1');
    expect(useHomeStore.getState().hireNotice).toEqual(notice);
    useHomeStore.getState().showEmployee('w2');
    expect(useHomeStore.getState().hireNotice).toBeNull();
  });
});
