/**
 * ConnectAIStep behavioural tests.
 *
 * The catalogue hook is module-mocked (simpler and more robust than seeding
 * the TanStack Query cache + real WebSocket plumbing -- the component only
 * reads `catalogueQuery.data`). The WebSocketContext is also full-module
 * replaced defensively so no transitive import ever reaches the real
 * provider (the importActual+spread variant is documented broken under
 * React 19 -- see CredentialsModal.test.tsx).
 *
 * Locks in:
 *   - only `category === 'ai'` providers render
 *   - featured cards render in openai / anthropic / gemini order with the
 *     correct "Get a key" hrefs + target/rel hardening
 *   - stored provider flips: Connected badge, "You're connected" header,
 *     "Manage AI accounts" CTA
 *   - none stored: "Connect your AI" header, "Connect your AI account" CTA,
 *     trust microcopy
 *   - data undefined: skeletons render and the CTA stays usable
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../../test/providers';

import type {
  CatalogueResponse,
  ServerProviderConfig,
} from '../../../hooks/useCatalogueQuery';

// --- Mocks (declared BEFORE importing the component -- vi.mock is hoisted) --

let catalogueData: CatalogueResponse | undefined;

vi.mock('../../../hooks/useCatalogueQuery', () => ({
  useCatalogueQuery: () => ({ data: catalogueData }),
}));

// Defensive full-module replace: nothing in this render tree should touch
// the real WebSocket provider.
vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocket: () => ({
    isConnected: true,
    isReady: true,
    sendRequest: vi.fn().mockResolvedValue({}),
  }),
}));

import ConnectAIStep from '../steps/ConnectAIStep';

// --- Fixtures ---------------------------------------------------------------

const makeProvider = (
  over: Partial<ServerProviderConfig> & { id: string; name: string },
): ServerProviderConfig => ({
  category: 'ai',
  category_label: 'AI Providers',
  color: '#8be9fd',
  kind: 'apiKey',
  icon_ref: '\u{1F916}',
  stored: false,
  ...over,
});

const baseProviders = (): ServerProviderConfig[] => [
  makeProvider({ id: 'openai', name: 'OpenAI' }),
  makeProvider({ id: 'anthropic', name: 'Anthropic' }),
  makeProvider({ id: 'gemini', name: 'Gemini' }),
  // Non-featured ai provider -> renders in the "more providers" chip row.
  makeProvider({ id: 'groq', name: 'Groq' }),
  // Non-ai provider -> must never render on this step.
  makeProvider({
    id: 'google',
    name: 'Google Workspace',
    category: 'google',
    category_label: 'Google',
    kind: 'oauth',
  }),
];

const catalogue = (providers: ServerProviderConfig[]): CatalogueResponse => ({
  providers,
  categories: [],
  version: 'test-v1',
});

const renderStep = (onOpenCredentials: () => void = vi.fn()) =>
  renderWithProviders(<ConnectAIStep onOpenCredentials={onOpenCredentials} />);

beforeEach(() => {
  vi.clearAllMocks();
  catalogueData = catalogue(baseProviders());
});

// --- Tests ------------------------------------------------------------------

describe('ConnectAIStep provider filtering', () => {
  it('renders only ai-category providers', () => {
    renderStep();
    // Featured card.
    expect(screen.getByText('Gemini')).toBeInTheDocument();
    // Non-featured ai provider appears in the chip row.
    expect(screen.getByText('Groq')).toBeInTheDocument();
    // Non-ai category providers are filtered out entirely.
    expect(screen.queryByText('Google Workspace')).not.toBeInTheDocument();
  });
});

describe('ConnectAIStep featured cards', () => {
  it('renders featured cards in openai/anthropic/gemini order with hardened key links', () => {
    renderStep();

    const links = screen.getAllByRole('link', { name: /get a key/i });
    expect(links.map((l) => l.getAttribute('href'))).toEqual([
      'https://platform.openai.com/api-keys',
      'https://console.anthropic.com/settings/keys',
      'https://aistudio.google.com/apikey',
    ]);
    for (const link of links) {
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    }
  });
});

describe('ConnectAIStep stored state', () => {
  it('shows Connected badge, connected header, and Manage CTA when anthropic is stored', () => {
    catalogueData = catalogue(
      baseProviders().map((p) => (p.id === 'anthropic' ? { ...p, stored: true } : p)),
    );
    renderStep();

    // Badge on the stored card.
    expect(screen.getByText('Connected')).toBeInTheDocument();
    // Header flips (curly apostrophe in the source -- match loosely).
    expect(screen.getByText(/You.re connected/)).toBeInTheDocument();
    expect(screen.queryByText('Connect your AI')).not.toBeInTheDocument();
    // CTA flips.
    expect(
      screen.getByRole('button', { name: 'Manage AI accounts' }),
    ).toBeInTheDocument();
    // Stored card no longer offers a "Get a key" link; the other two do.
    expect(screen.getAllByRole('link', { name: /get a key/i })).toHaveLength(2);
  });

  it('shows connect header, connect CTA, and trust microcopy when nothing is stored', () => {
    renderStep();

    expect(screen.getByText('Connect your AI')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Connect your AI account' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Your key is saved only on this device/),
    ).toBeInTheDocument();
    expect(screen.queryByText('Connected')).not.toBeInTheDocument();
  });
});

describe('ConnectAIStep loading state', () => {
  it('renders skeletons while the catalogue is loading and keeps the CTA usable', async () => {
    catalogueData = undefined;
    const onOpenCredentials = vi.fn();
    const { container } = renderStep(onOpenCredentials);

    // One skeleton per featured provider.
    expect(container.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(3);
    // No provider cards yet.
    expect(screen.queryByText('OpenAI')).not.toBeInTheDocument();

    // CTA renders (loading counts as "not connected") and still fires.
    const cta = screen.getByRole('button', { name: 'Connect your AI account' });
    await userEvent.click(cta);
    expect(onOpenCredentials).toHaveBeenCalledTimes(1);
  });
});

describe('ConnectAIStep CTA', () => {
  it('clicking the CTA calls onOpenCredentials exactly once', async () => {
    const onOpenCredentials = vi.fn();
    renderStep(onOpenCredentials);

    await userEvent.click(
      screen.getByRole('button', { name: 'Connect your AI account' }),
    );
    expect(onOpenCredentials).toHaveBeenCalledTimes(1);
  });
});
