import React from 'react';
import { KeyRound, Check, ExternalLink, ShieldCheck } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { NodeIcon } from '../../../assets/icons';
import { useCatalogueQuery, type ServerProviderConfig } from '../../../hooks/useCatalogueQuery';
import { FEATURED_AI_PROVIDERS } from '../aiProviderLinks';

interface ConnectAIStepProps {
  onOpenCredentials: () => void;
}

const ConnectAIStep: React.FC<ConnectAIStepProps> = ({ onOpenCredentials }) => {
  const catalogueQuery = useCatalogueQuery();
  const providers = catalogueQuery.data?.providers;

  const aiProviders = React.useMemo(
    () => (providers ?? []).filter((p) => p.category === 'ai'),
    [providers],
  );
  const featured = FEATURED_AI_PROVIDERS
    .map((entry) => {
      const provider = aiProviders.find((p) => p.id === entry.id);
      return provider ? { ...entry, provider } : null;
    })
    .filter((entry): entry is { id: string; hint: string; keyUrl: string; provider: ServerProviderConfig } => entry !== null);
  const featuredIds = new Set(FEATURED_AI_PROVIDERS.map((entry) => entry.id));
  const moreProviders = aiProviders.filter((p) => !featuredIds.has(p.id));
  const connected = aiProviders.some((p) => p.stored);
  const isLoading = providers === undefined;

  return (
    <div className="py-1">
      <div className="mb-4 text-center">
        <h4 className="m-0 mb-1 flex items-center justify-center gap-2 text-lg font-semibold">
          {connected ? (
            <>
              <Check className="h-4 w-4 text-success" />
              You&apos;re connected
            </>
          ) : (
            <>
              <KeyRound className="h-4 w-4 text-warning" />
              Connect your AI
            </>
          )}
        </h4>
        <p className="text-xs text-muted-foreground">
          {connected
            ? 'Nice — your AI is ready to think.'
            : 'Your agents need an AI account to think. Paste one key — it takes about a minute.'}
        </p>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-3 gap-2">
          {FEATURED_AI_PROVIDERS.map((entry) => (
            <Skeleton key={entry.id} className="h-24 w-full" />
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2">
            {featured.map(({ id, hint, keyUrl, provider }) => (
              <div
                key={id}
                className="flex flex-col items-center gap-1 rounded-md border border-border bg-muted/50 px-3 py-3 text-center"
              >
                <NodeIcon icon={provider.icon_ref} className="h-6 w-6 shrink-0 text-lg" />
                <span className="text-sm font-semibold">{provider.name}</span>
                <span className="text-xs text-muted-foreground">{hint}</span>
                {provider.stored ? (
                  <Badge variant="success" className="mt-1">
                    <Check className="h-3 w-3" />
                    Connected
                  </Badge>
                ) : (
                  <a
                    href={keyUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 inline-flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
                  >
                    <ExternalLink className="h-3 w-3" /> Get a key
                  </a>
                )}
              </div>
            ))}
          </div>

          {moreProviders.length > 0 && (
            <div className="mt-3 text-center">
              <div className="flex flex-wrap items-center justify-center gap-1.5">
                {moreProviders.map((p) => (
                  <span
                    key={p.id}
                    className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/50 px-2 py-0.5 text-xs"
                  >
                    <NodeIcon icon={p.icon_ref} className="h-3.5 w-3.5 shrink-0 text-sm" />
                    {p.name}
                    {p.stored && <Check className="h-3 w-3 text-success" />}
                  </span>
                ))}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                No account yet? Ollama and LM Studio run free models right on your computer — pick
                one from the list.
              </p>
            </div>
          )}
        </>
      )}

      <div className="mt-5 text-center">
        <Button
          variant={connected ? 'outline' : 'default'}
          onClick={onOpenCredentials}
          className="gap-2"
        >
          <KeyRound className="h-4 w-4" />
          {connected ? 'Manage AI accounts' : 'Connect your AI account'}
        </Button>
      </div>

      {connected ? (
        <Alert variant="success" className="mt-4">
          <AlertDescription className="text-xs">
            You can add another AI account or change your key anytime — click the key button in the
            toolbar.
          </AlertDescription>
        </Alert>
      ) : (
        <p className="mt-4 flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
          <ShieldCheck className="h-3.5 w-3.5" />
          Your key is saved only on this device. It&apos;s never shared with anyone except the AI you
          choose.
        </p>
      )}
    </div>
  );
};

export default ConnectAIStep;
