/**
 * The Avatar size the onboarding handoff added: 48px on a setup card's
 * identity row (`card`), with the thicker ring and an initial to match.
 * And the solid accent face (chat V2 colour pass) an employee wears on
 * their own page in place of their role tint.
 */

import { describe, expect, it } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import { Avatar } from '../ui/primitives';

describe('Avatar', () => {
  it('keeps the assigned portrait through renames and restores it after removing a custom photo', () => {
    const { container, rerender } = render(<Avatar name="Maya" colorRole="agent" fallbackPhoto="/cat.png" />);
    expect(container.querySelector('img')).toHaveAttribute('src', '/cat.png');
    rerender(<Avatar name="Renamed employee" colorRole="agent" photo="/custom.png" fallbackPhoto="/cat.png" />);
    expect(container.querySelector('img')).toHaveAttribute('src', '/custom.png');
    rerender(<Avatar name="Renamed employee" colorRole="agent" photo={null} fallbackPhoto="/cat.png" />);
    expect(container.querySelector('img')).toHaveAttribute('src', '/cat.png');
  });

  it('falls back from a broken custom photo to the cat, then to an initial if both fail', () => {
    const { container, rerender } = render(<Avatar name="Maya" colorRole="agent" photo="/custom.png" fallbackPhoto="/cat.png" />);
    fireEvent.error(container.querySelector('img')!);
    expect(container.querySelector('img')).toHaveAttribute('src', '/cat.png');
    fireEvent.error(container.querySelector('img')!);
    expect(container.querySelector('img')).toBeNull();
    expect(container.firstElementChild).toHaveTextContent('M');
    // A later successful upload is still eligible after previous URLs failed.
    rerender(<Avatar name="Maya" colorRole="agent" photo="/new.png" fallbackPhoto="/cat.png" />);
    expect(container.querySelector('img')).toHaveAttribute('src', '/new.png');
  });

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

  it('takes no role tint when plain: a draft, and the owner', () => {
    const { container } = render(<Avatar name="Skye" colorRole="agent" plain />);
    expect(container.firstElementChild).toHaveClass('border-border-default', 'bg-bg-hover', 'text-fg-default');
    expect(container.firstElementChild?.className).not.toMatch(/node-/);
    expect(container.firstElementChild).toHaveTextContent('S');
  });
});
