import { describe, expect, it, vi } from 'vitest';
import { matchProvider, runSpecAction, type SpecActionHandlers } from '../actions';

const providers = [
  { providerId: 'whatsapp', name: 'WhatsApp' },
  { providerId: 'whatsapp_business', name: 'WhatsApp Business' },
  { providerId: 'google', name: 'Google Workspace' },
  { providerId: 'stripe', name: 'Stripe' },
];

function handlers(): SpecActionHandlers & Record<string, ReturnType<typeof vi.fn>> {
  return {
    setValue: vi.fn(),
    refine: vi.fn(),
    openConnectors: vi.fn(),
    connect: vi.fn(),
    hire: vi.fn(),
  } as never;
}

describe('matchProvider', () => {
  it('prefers an exact name, then the longest prefix, then a substring', () => {
    expect(matchProvider('whatsapp', providers)?.providerId).toBe('whatsapp');
    expect(matchProvider('WhatsApp Business account', providers)?.providerId).toBe('whatsapp_business');
    expect(matchProvider('Google', providers)?.providerId).toBe('google');
    expect(matchProvider('workspace', providers)?.providerId).toBe('google');
  });

  it('needs three characters for a substring match', () => {
    expect(matchProvider('pe', providers)).toBeNull();
    expect(matchProvider('ripe', providers)?.providerId).toBe('stripe');
    expect(matchProvider('xyz', providers)).toBeNull();
    expect(matchProvider('', providers)).toBeNull();
  });
});

describe('runSpecAction', () => {
  const apps = {
    gmail: { app_id: 'gmail', provider_id: 'google', name: 'Gmail', icon_ref: null, connected: false, supported: true },
    quickbooks: { app_id: 'quickbooks', provider_id: '', name: 'QuickBooks', icon_ref: null, connected: false, supported: false },
  };
  const context = { state: { freq: 'weekly' }, apps, providers };

  it('connects an app the server resolved, by its provider', () => {
    const h = handlers();
    runSpecAction('connect_app', { app: 'Gmail' }, context, h);
    expect(h.connect).toHaveBeenCalledWith('google', 'Gmail');
  });

  it('falls back to the provider names, and opens Connectors when nothing matches', () => {
    const h = handlers();
    runSpecAction('connect_app', { app: 'Stripe' }, context, h);
    expect(h.connect).toHaveBeenCalledWith('stripe', 'Stripe');
    runSpecAction('connect_app', { app: 'QuickBooks' }, context, h);
    runSpecAction('connect_app', {}, context, h);
    expect(h.openConnectors).toHaveBeenCalledTimes(2);
  });

  it('resolves params against the screen state', () => {
    const h = handlers();
    runSpecAction('setState', { statePath: '/choices/freq', value: { $state: '/freq' } }, context, h);
    expect(h.setValue).toHaveBeenCalledWith('/choices/freq', 'weekly');
    runSpecAction('hire_employee', { name: 'Maya', note: { $template: 'Reports ${/freq}' } }, context, h);
    expect(h.hire).toHaveBeenCalledWith({ name: 'Maya', note: 'Reports weekly' });
  });

  it('treats ask as a plain change request and ignores unknown actions', () => {
    const h = handlers();
    runSpecAction('ask', { prompt: 'Only weekdays' }, context, h);
    expect(h.refine).toHaveBeenCalledTimes(1);
    expect(h.refine).toHaveBeenCalledWith();
    runSpecAction('delete_everything', {}, context, h);
    expect(Object.values(h).every((fn) => fn.mock.calls.length <= 1)).toBe(true);
  });
});
