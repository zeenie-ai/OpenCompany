/**
 * The edge condition dialog gets the width it asks for (the dialog
 * primitive's own `sm:max-w-sm` beats an unprefixed one, so it had drawn at
 * 384px), and its buttons follow the colour rule: Cancel a neutral outline,
 * Save the one strong button.
 */

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import EdgeConditionEditor from '../EdgeConditionEditor';

describe('EdgeConditionEditor', () => {
  it('is as wide as it asks, with a neutral Cancel and an invert Save', () => {
    render(<EdgeConditionEditor isOpen onClose={vi.fn()} onSave={vi.fn()} sourceNodeName="Fetch orders" />);

    expect(screen.getByRole('dialog')).toHaveClass('sm:max-w-[420px]');
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(cancel).toHaveAttribute('data-variant', 'quiet');
    expect(cancel).toHaveClass('border-border-default', 'text-fg-default');
    expect(screen.getByRole('button', { name: 'Save Condition' })).toHaveAttribute('data-variant', 'invert');
    expect(screen.getByRole('dialog').innerHTML).not.toMatch(/text-accent|bg-primary/);
  });
});
