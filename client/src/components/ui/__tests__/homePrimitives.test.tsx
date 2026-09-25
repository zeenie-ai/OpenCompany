/**
 * The primitive extensions Normal mode builds on: Modal (single close,
 * header-less, spring entrance), Switch (md size, run tone), Textarea (bare),
 * Button (invert, chip, pill), ToggleGroup.
 */
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import Modal from '../Modal';
import { Switch } from '../switch';
import { Textarea } from '../textarea';
import { Button } from '../button';
import { ToggleGroup, ToggleGroupItem } from '../toggle-group';

describe('Modal', () => {
  it('closes exactly once from the close button', () => {
    const onClose = vi.fn();
    render(
      <Modal isOpen onClose={onClose} title="Settings">
        body
      </Modal>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('can drop the title bar and keep the accessible name', () => {
    render(
      <Modal isOpen onClose={() => {}} title="Settings" hideHeader>
        <p>body</p>
      </Modal>,
    );
    expect(screen.getByRole('dialog', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull();
  });

  it('shows no title icon when titleIcon is null', () => {
    render(
      <Modal isOpen onClose={() => {}} title="Connect Gmail" titleIcon={null}>
        body
      </Modal>,
    );
    // The heading, not getByText: the sr-only description repeats the title.
    const title = screen.getByRole('heading', { name: 'Connect Gmail' });
    expect(title.querySelector('svg')).toBeNull();
  });

  it('keeps the gear icon by default', () => {
    render(
      <Modal isOpen onClose={() => {}} title="Parameters">
        body
      </Modal>,
    );
    expect(screen.getByRole('heading', { name: 'Parameters' }).querySelector('svg')).not.toBeNull();
  });

  it('uses the spring entrance only when asked, and sets the body face', () => {
    const { rerender } = render(
      <Modal isOpen onClose={() => {}} title="A">
        body
      </Modal>,
    );
    const dialog = () => screen.getByRole('dialog');
    expect(dialog().className).toContain('font-body');
    expect(dialog().className).toContain('zoom-in-95');
    expect(dialog().className).not.toContain('--dur-panel-in');
    rerender(
      <Modal isOpen onClose={() => {}} title="A" motion="spring">
        body
      </Modal>,
    );
    expect(dialog().className).toContain('data-[state=open]:duration-(--dur-panel-in)');
    expect(dialog().className).toContain('motion-reduce:animate-none!');
    expect(dialog().className).not.toContain('zoom-in-95');
  });
});

describe('Switch', () => {
  it('md size + run tone carry only the run colours', () => {
    render(<Switch size="md" tone="run" aria-label="Memory" defaultChecked />);
    const root = screen.getByRole('switch', { name: 'Memory' });
    expect(root).toHaveAttribute('data-size', 'md');
    expect(root.className).toContain('data-checked:bg-action-run-hover');
    expect(root.className).not.toContain('bg-primary');
    const thumb = root.querySelector('[data-slot="switch-thumb"]')!;
    expect(thumb.className).toContain('data-checked:bg-action-run-ink');
    expect(thumb.className).not.toContain('dark:data-checked:bg-primary-foreground');
  });

  it('toggles', () => {
    const onCheckedChange = vi.fn();
    render(<Switch aria-label="Local" onCheckedChange={onCheckedChange} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Local' }));
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });
});

describe('Textarea', () => {
  it('bare drops the field chrome and the per-theme input hook', () => {
    render(<Textarea variant="bare" aria-label="Job" />);
    const el = screen.getByRole('textbox', { name: 'Job' });
    expect(el.classList.contains('input')).toBe(false);
    expect(el.className).not.toContain('border-input');
  });

  it('default keeps them', () => {
    render(<Textarea aria-label="Notes" />);
    expect(screen.getByRole('textbox', { name: 'Notes' }).classList.contains('input')).toBe(true);
  });
});

describe('Button', () => {
  it('invert keeps full opacity when disabled, with the neutral fill', () => {
    render(
      <Button variant="invert" size="pill" disabled>
        Create employee
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Create employee' });
    expect(button.className).toContain('disabled:opacity-100');
    expect(button.className).not.toContain('disabled:opacity-50');
    expect(button.className).toContain('rounded-pill');
    expect(button.className).not.toContain('rounded-lg');
  });

  it('chip takes role tints from className', () => {
    render(
      <Button variant="chip" size="chip" className="bg-node-agent-soft text-node-agent-ink">
        Receptionist
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Receptionist' });
    expect(button.className).toContain('bg-node-agent-soft');
    expect(button.className).not.toContain('bg-transparent');
    expect(button.className).not.toContain('text-fg-default');
  });
});

describe('ToggleGroup', () => {
  function Choice() {
    const [value, setValue] = useState('weekly');
    return (
      <ToggleGroup
        type="single"
        variant="segmented"
        aria-label="Summary"
        value={value}
        onValueChange={(next) => next && setValue(next)}
      >
        <ToggleGroupItem value="weekly">weekly</ToggleGroupItem>
        <ToggleGroupItem value="monthly">monthly</ToggleGroupItem>
      </ToggleGroup>
    );
  }

  it('selects one item at a time', () => {
    render(<Choice />);
    const weekly = screen.getByRole('radio', { name: 'weekly' });
    const monthly = screen.getByRole('radio', { name: 'monthly' });
    expect(weekly).toHaveAttribute('data-state', 'on');
    fireEvent.click(monthly);
    expect(monthly).toHaveAttribute('data-state', 'on');
    expect(weekly).toHaveAttribute('data-state', 'off');
  });

  it('draws the track and passes the variant to its items', () => {
    render(<Choice />);
    const group = screen.getByRole('radiogroup', { name: 'Summary' });
    expect(group.className).toContain('bg-bg-app');
    for (const item of screen.getAllByRole('radio')) {
      expect(item).toHaveAttribute('data-variant', 'segmented');
    }
  });
});
