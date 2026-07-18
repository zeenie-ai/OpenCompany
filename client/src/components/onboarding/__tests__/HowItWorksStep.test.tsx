/**
 * HowItWorksStep behavioural tests.
 *
 * useNodeGroups is module-mocked (full replace) so the step renders against
 * a controlled groups payload without WebSocket / TanStack plumbing.
 *
 * Locks in:
 *   - Badge chips render ONLY for groups with visibility 'normal' or 'all'
 *     ('dev' groups are filtered out)
 *   - when groups data is undefined, the sentence renders without chips and
 *     the three concept cards still render
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/providers';

import type { NodeGroupEntry } from '../../../lib/nodeSpec';

// --- Mocks (declared BEFORE importing the component -- vi.mock is hoisted) --

let groupsData: Record<string, NodeGroupEntry> | undefined;

vi.mock('../../../lib/nodeSpec', () => ({
  useNodeGroups: () => ({ data: groupsData }),
}));

import HowItWorksStep from '../steps/HowItWorksStep';

// --- Fixtures ---------------------------------------------------------------

const entry = (
  label: string,
  visibility: NodeGroupEntry['visibility'],
): NodeGroupEntry => ({
  types: [],
  label,
  icon: '\u{1F916}',
  color: '#bd93f9',
  visibility,
});

const CONCEPT_TITLES = [
  'Snap blocks together',
  'Agents do the thinking',
  'Chat to make it go',
];

beforeEach(() => {
  groupsData = undefined;
});

// --- Tests ------------------------------------------------------------------

describe('HowItWorksStep group chips', () => {
  it('renders chips for normal + all visibility groups and filters out dev groups', () => {
    groupsData = {
      agent: entry('AI Agents', 'normal'),
      model: entry('AI Models', 'all'),
      android: entry('Android', 'dev'),
    };
    renderWithProviders(<HowItWorksStep />);

    expect(screen.getByText('AI Agents')).toBeInTheDocument();
    expect(screen.getByText('AI Models')).toBeInTheDocument();
    expect(screen.queryByText('Android')).not.toBeInTheDocument();
  });

  it('renders the toolbar sentence without chips when groups data is undefined', () => {
    groupsData = undefined;
    const { container } = renderWithProviders(<HowItWorksStep />);

    expect(container.querySelectorAll('[data-slot="badge"]')).toHaveLength(0);
    expect(
      screen.getByText(/Normal shows just the AI blocks/),
    ).toBeInTheDocument();
  });
});

describe('HowItWorksStep concept cards', () => {
  it('always renders the three concept cards (with groups data)', () => {
    groupsData = { agent: entry('AI Agents', 'normal') };
    renderWithProviders(<HowItWorksStep />);
    for (const title of CONCEPT_TITLES) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
  });

  it('always renders the three concept cards (without groups data)', () => {
    groupsData = undefined;
    renderWithProviders(<HowItWorksStep />);
    for (const title of CONCEPT_TITLES) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
  });
});
