/**
 * The answering employee's face beside their turns (chat V2 colour pass): the
 * solid accent disc whatever their role, their photo on it when they have
 * one, and the live ring while they work.
 */

import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { ChatAvatar } from '../thread/ChatAvatar';

describe('ChatAvatar', () => {
  it('draws the initial on the accent disc, not the role tint', () => {
    const { container } = render(<ChatAvatar persona={{ name: 'Maya', colorRole: 'tool' }} />);
    const face = container.querySelector('.bg-accent');
    expect(face).toHaveClass('text-accent-foreground', 'border-accent-edge', 'shadow-avatar');
    expect(face).toHaveTextContent('M');
    expect(container.innerHTML).not.toMatch(/node-tool/);
    expect(container.querySelector('[data-live-ring]')).toBeNull();
  });

  it('puts their photo on the disc, and rings it while they work', () => {
    const { container } = render(<ChatAvatar persona={{ name: 'Maya', colorRole: 'tool', photo: '/p.png' }} live />);
    expect(container.querySelector('.bg-accent img')).toHaveAttribute('src', '/p.png');
    expect(container.querySelector('[data-live-ring]')).not.toBeNull();
  });
});
