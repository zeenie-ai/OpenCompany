/**
 * The Avatar sizes the onboarding handoff added: 48px on a setup card's
 * identity row (`card`) and 72px for a new hire arriving on their page
 * (`xl`), each with the thicker ring and an initial to match.
 */

import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { Avatar } from '../ui/primitives';

describe('Avatar', () => {
  it.each([
    ['card', ['size-12', 'border-2', 'text-lg']],
    ['xl', ['size-18', 'border-2', 'text-headline']],
  ] as const)('draws the %s size', (size, classes) => {
    const { container } = render(<Avatar name="Maya" colorRole="agent" size={size} />);
    const avatar = container.firstElementChild;
    expect(avatar).toHaveClass(...classes);
    expect(avatar).toHaveTextContent('M');
  });
});
