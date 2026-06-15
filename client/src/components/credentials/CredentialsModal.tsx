/**
 * CredentialsModal — thin shell.
 *
 * Post-Wave-12: the server-owned catalogue (`get_credential_catalogue`
 * → `useCatalogueQuery`) is the SINGLE source of truth for the
 * provider list. The retired `providers.tsx` static fallback no longer
 * exists — adding a new provider is a backend-only change.
 *
 * Cold-boot UX:
 *   - With IDB hit (return visit): catalogue populated within ~50 ms
 *     via the warm-start path in `useCatalogueQuery`.
 *   - With IDB miss (first visit / cleared storage): a Skeleton
 *     palette renders while the WS catalogue arrives (~200-500 ms).
 *   - Server unreachable: explicit "couldn't reach server" error
 *     state — never a stale fallback list (would mislead the user
 *     about which providers they have configured).
 */

import React, { useMemo } from 'react';
import { Loader2, ShieldCheck, AlertTriangle } from 'lucide-react';

import Modal from '../ui/Modal';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import PanelRenderer from './PanelRenderer';
import CredentialsPalette from './CredentialsPalette';
import { rehydrateCatalogue } from './catalogueAdapter';
import { useCatalogueQuery } from '../../hooks/useCatalogueQuery';
import { useCredentialRegistry } from '../../store/useCredentialRegistry';
import { useNodeAllowlist } from '../../hooks/useNodeAllowlist';

interface Props {
  visible: boolean;
  onClose: () => void;
}

const CredentialsModal: React.FC<Props> = ({ visible, onClose }) => {
  // UI state lives in the Zustand store (no catalogue data ever).
  const selectedId = useCredentialRegistry((s) => s.selectedId);
  const setSelectedId = useCredentialRegistry((s) => s.setSelectedId);

  // Server-owned catalogue with IDB warm-start. Single source of truth.
  const catalogue = useCatalogueQuery();

  // Rehydrate server JSON → runtime ProviderConfig shape. No client
  // fallback — if the data isn't here yet we render Skeleton, and if
  // the fetch errored we render an explicit error state.
  const rehydrated = useMemo(() => {
    if (!catalogue.data) return null;
    return rehydrateCatalogue(catalogue.data);
  }, [catalogue.data]);

  // Apply the absolute blocklist from server/config/node_allowlist.json
  // (`disabled_credential_categories`). Hides the entire category +
  // every provider belonging to it. Mode-independent; complements the
  // node-side `disabled_groups` so disabling Android removes both the
  // canvas nodes AND the credentials panel in one config edit.
  const { isCredentialCategoryDisabled } = useNodeAllowlist();
  const providers = useMemo(
    () =>
      (rehydrated?.providers ?? []).filter(
        (p) => !isCredentialCategoryDisabled(p.category),
      ),
    [rehydrated?.providers, isCredentialCategoryDisabled],
  );
  const categories = useMemo(
    () =>
      (rehydrated?.categories ?? []).filter(
        (c) => !isCredentialCategoryDisabled(c.key),
      ),
    [rehydrated?.categories, isCredentialCategoryDisabled],
  );

  // Default selection: if nothing is selected yet (or the previous
  // selection isn't in the current catalogue), pick the first provider.
  const effectiveSelectedId = useMemo(() => {
    if (selectedId && providers.some((p) => p.id === selectedId)) return selectedId;
    return providers[0]?.id ?? null;
  }, [selectedId, providers]);

  // Keep the store in sync without causing a render loop — only update
  // when the effective id diverges from the stored one.
  React.useEffect(() => {
    if (effectiveSelectedId && effectiveSelectedId !== selectedId) {
      setSelectedId(effectiveSelectedId);
    }
  }, [effectiveSelectedId, selectedId, setSelectedId]);

  const selected = useMemo(
    () => providers.find((p) => p.id === effectiveSelectedId) ?? null,
    [providers, effectiveSelectedId],
  );

  const isLoadingServer = catalogue.isLoading && !catalogue.data;
  const hasServerError = catalogue.isError && !catalogue.data;

  const headerActions = (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 text-base font-semibold">
        <ShieldCheck className="h-4 w-4 text-warning" />
        <span>API Credentials</span>
      </div>
      {rehydrated && (
        <Badge variant="success">{providers.length} providers</Badge>
      )}
      {isLoadingServer && (
        <Badge variant="info" className="gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          loading
        </Badge>
      )}
      {hasServerError && (
        <Badge variant="destructive" className="gap-1" title="Server unreachable">
          <AlertTriangle className="h-3 w-3" />
          offline
        </Badge>
      )}
    </div>
  );

  // Body dispatch:
  //   1. Server error + no cached data → error state, no providers shown.
  //   2. Loading + no cached data → Skeleton palette + empty detail.
  //   3. Catalogue available (cached or fresh) → normal UI.
  let body: React.ReactNode;
  if (hasServerError) {
    body = (
      <div className="flex flex-1 flex-col items-center justify-center p-8">
        <Alert variant="destructive" className="max-w-md">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Couldn't reach the credentials server</AlertTitle>
          <AlertDescription>
            The provider list comes from the backend. Check your connection
            and try again — refreshing the page will retry the fetch.
          </AlertDescription>
        </Alert>
      </div>
    );
  } else if (!rehydrated) {
    body = (
      <div className="flex h-full overflow-hidden">
        <div className="flex w-[280px] shrink-0 flex-col gap-2 border-r border-border p-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
        <div className="flex flex-1 flex-col gap-3 bg-background p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-4 w-72" />
          <Skeleton className="mt-4 h-32 w-full" />
        </div>
      </div>
    );
  } else {
    body = (
      <div className="flex h-full overflow-hidden">
        <div className="flex w-[280px] shrink-0 flex-col border-r border-border">
          <CredentialsPalette
            providers={providers}
            categories={categories}
            selectedId={effectiveSelectedId}
            onSelect={setSelectedId}
          />
        </div>
        <div className="flex flex-1 flex-col overflow-auto bg-background">
          <PanelRenderer config={selected} visible={visible} />
        </div>
      </div>
    );
  }

  return (
    <Modal
      isOpen={visible}
      onClose={onClose}
      maxWidth="95vw"
      maxHeight="95vh"
      headerActions={headerActions}
    >
      {body}
    </Modal>
  );
};

export default CredentialsModal;
