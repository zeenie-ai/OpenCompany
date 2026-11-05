/**
 * The Avatar size the onboarding handoff added: 48px on a setup card's
 * identity row (`card`), with the thicker ring and an initial to match.
 * And the solid accent face (chat V2 colour pass) an employee wears on
 * their own page in place of their role tint.
 */

import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { Avatar } from '../ui/primitives';

describe('Avatar', () => {
  it.each([
    ['card', ['size-12', 'border-2', 'text-lg']],
  ] as const)('draws the %s size', (size, classes) => {
    const { container } = render(<Avatar name="Maya" colorRole="agent" size={size} />);
    const avatar = container.firstElementChild;
    expect(avatar).toHaveClass(...classes);
    expect(avatar).toHaveTextContent('M');
  });

  it('wears the accent face when asked, and its role tint otherwise', () => {
    const { container, rerender } = render(<Avatar name="Maya" colorRole="tool" accent />);
    const avatar = () => container.firstElementChild;
    expect(avatar()).toHaveClass('bg-accent', 'text-accent-foreground', 'border-accent-edge', 'shadow-avatar');
    expect(avatar()).not.toHaveClass('bg-node-tool-fill');
    expect(avatar()).toHaveTextContent('M');
    rerender(<Avatar name="Maya" colorRole="tool" />);
    expect(avatar()).toHaveClass('bg-node-tool-fill', 'border-node-tool-edge');
    expect(avatar()).not.toHaveClass('bg-accent');
  });
});
