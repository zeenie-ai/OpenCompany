/**
 * LlmUsageSection — token usage and costs for an AI provider.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { DollarSign, RefreshCw, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from '@/components/ui/accordion';
import { useApiKeys, type ProviderUsageSummary } from '../../../hooks/useApiKeys';

interface Props {
  providerId: string;
  providerName: string;
}

const Stat: React.FC<{ label: string; value: React.ReactNode; className?: string }> = ({
  label,
  value,
  className,
}) => (
  <div className="flex flex-col">
    <span className="text-xs text-muted-foreground">{label}</span>
    <span className={`text-lg font-semibold ${className ?? ''}`}>{value}</span>
  </div>
);

const LlmUsageSection: React.FC<Props> = ({ providerId, providerName }) => {
  const { getProviderUsageSummary, isConnected } = useApiKeys();
  const [data, setData] = useState<ProviderUsageSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async () => {
    if (!isConnected) return;
    setLoading(true);
    try {
      const summary = await getProviderUsageSummary();
      setData(summary.find((p) => p.provider === providerId) ?? null);
    } finally {
      setLoading(false);
    }
  }, [isConnected, providerId, getProviderUsageSummary]);

  useEffect(() => {
    if (expanded) load();
  }, [expanded, load]);

  return (
    <Accordion
      type="single"
      collapsible
      onValueChange={(value) => setExpanded(value === 'usage')}
    >
      <AccordionItem value="usage">
        <AccordionTrigger>
          <span className="flex items-center gap-2">
            <DollarSign className="h-4 w-4" /> Usage &amp; Costs
          </span>
        </AccordionTrigger>
        <AccordionContent>
          {loading ? (
            <div className="flex justify-center p-4">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          ) : !data || data.execution_count === 0 ? (
            <Alert variant="info">
              <AlertDescription>No usage data yet for {providerName}</AlertDescription>
            </Alert>
          ) : (
            <div className="flex w-full flex-col gap-4">
              <div className="flex flex-wrap gap-4">
                <Stat label="Total Tokens" value={data.total_tokens.toLocaleString()} className="text-info" />
                <Stat label="Total Cost" value={`$${data.total_cost.toFixed(4)}`} className="text-success" />
                <Stat label="Executions" value={data.execution_count} className="text-primary" />
              </div>

              <div className="overflow-hidden rounded-md border border-border">
                <div className="grid grid-cols-2 divide-x divide-border border-b border-border">
                  <div className="px-3 py-2 text-sm">
                    <div className="text-xs text-muted-foreground">Input Tokens</div>
                    <div>
                      {data.total_input_tokens.toLocaleString()}{' '}
                      <span className="ml-1 text-success">
                        (${data.total_input_cost.toFixed(4)})
                      </span>
                    </div>
                  </div>
                  <div className="px-3 py-2 text-sm">
                    <div className="text-xs text-muted-foreground">Output Tokens</div>
                    <div>
                      {data.total_output_tokens.toLocaleString()}{' '}
                      <span className="ml-1 text-success">
                        (${data.total_output_cost.toFixed(4)})
                      </span>
                    </div>
                  </div>
                </div>
                {data.total_cache_cost > 0 && (
                  <div className="px-3 py-2 text-sm">
                    <span className="text-xs text-muted-foreground">Cache Cost: </span>
                    <span className="text-success">${data.total_cache_cost.toFixed(4)}</span>
                  </div>
                )}
              </div>

              {data.models.length > 1 && (
                <div className="overflow-hidden rounded-md border border-border">
                  <div className="border-b border-border bg-muted px-3 py-1.5 text-xs font-semibold">
                    By Model
                  </div>
                  <div className="divide-y divide-border">
                    {data.models.map((m) => (
                      <div
                        key={m.model}
                        className="grid grid-cols-[1fr_auto] items-center gap-3 px-3 py-2 text-sm"
                      >
                        <code className="text-xs">{m.model}</code>
                        <span className="text-success">
                          ${m.total_cost.toFixed(4)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <Button size="sm" variant="outline" onClick={load} disabled={loading}>
                {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                Refresh
              </Button>
            </div>
          )}
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
};

export default LlmUsageSection;
