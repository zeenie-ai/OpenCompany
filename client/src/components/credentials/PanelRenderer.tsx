/**
 * PanelRenderer — lazy panel dispatch.
 *
 * Phase 5 of the credentials-scaling plan: each panel is loaded on
 * demand via `React.lazy` + Vite dynamic `import()`. First selection of
 * a given panel kind pays a one-time ~10–50 ms chunk-fetch cost; every
 * subsequent selection is instant. Reduces the credentials modal's
 * initial JS payload and lets Vite emit one chunk per panel kind.
 *
 * Panels are cached at module scope so switching providers of the same
 * kind does not trip Suspense on every click.
 */

import React, { Suspense, useMemo } from 'react';
import { Loader2, Info, AlertTriangle } from 'lucide-react';
import type { ProviderConfig, PanelKind } from './types';

// Vite will emit one chunk per panel under dist/assets/ApiKeyPanel-*.js,
// OAuthPanel-*.js, etc. Keep the import paths inside the arrow fns so
// code splitting is preserved.
const PANEL_LOADERS: Record<PanelKind, () => Promise<{ default: React.ComponentType<PanelProps> }>> = {
  apiKey: () => import('./panels/ApiKeyPanel'),
  oauth: () => import('./panels/OAuthPanel'),
  qrPairing: () => import('./panels/QrPairingPanel'),
  email: () => import('./panels/EmailPanel'),
  browserProfiles: () => import('./panels/BrowserProfilesPanel'),
};

// Memoize the lazy wrappers at module scope so switching providers of
// the same kind doesn't re-trigger Suspense. React.lazy memoizes too
// internally, but declaring them once makes the intent explicit and
// avoids any HMR edge cases.
const LAZY_PANELS: Record<PanelKind, React.LazyExoticComponent<React.ComponentType<PanelProps>>> = {
  apiKey: React.lazy(PANEL_LOADERS.apiKey),
  oauth: React.lazy(PANEL_LOADERS.oauth),
  qrPairing: React.lazy(PANEL_LOADERS.qrPairing),
  email: React.lazy(PANEL_LOADERS.email),
  browserProfiles: React.lazy(PANEL_LOADERS.browserProfiles),
};

/** `full`: the editor's Credentials modal (usage, provider defaults, rate
 *  limits). `compact`: only what it takes to connect, for Normal mode's
 *  Connect dialog. */
export type PanelVariant = 'full' | 'compact';

interface PanelProps {
  config: ProviderConfig;
  visible: boolean;
  variant?: PanelVariant;
}

interface Props {
  config: ProviderConfig | null;
  visible: boolean;
  variant?: PanelVariant;
}

const PanelFallback: React.FC = () => (
  <div className="flex h-full min-h-[240px] w-full items-center justify-center">
    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
  </div>
);

const EmptyState: React.FC<{ icon: React.ReactNode; message: string }> = ({ icon, message }) => (
  <div className="flex h-full w-full flex-col items-center justify-center gap-3 p-8 text-center">
    {icon}
    <p className="text-sm text-muted-foreground">{message}</p>
  </div>
);

const PanelRenderer: React.FC<Props> = ({ config, visible, variant = 'full' }) => {
  const Lazy = useMemo(() => {
    if (!config) return null;
    return LAZY_PANELS[config.kind] ?? null;
  }, [config]);

  if (!config) {
    return (
      <EmptyState
        icon={<Info className="h-8 w-8 text-muted-foreground" />}
        message="Select a credential to configure"
      />
    );
  }
  if (!Lazy) {
    return (
      <EmptyState
        icon={<AlertTriangle className="h-8 w-8 text-warning" />}
        message={`Unknown credential kind: ${config.kind}`}
      />
    );
  }

  return (
    <Suspense fallback={<PanelFallback />}>
      <Lazy config={config} visible={visible} variant={variant} />
    </Suspense>
  );
};

export default PanelRenderer;
