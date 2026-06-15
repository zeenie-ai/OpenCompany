/**
 * ApiUsageSection — generic API call stats for external services.
 * Used by Twitter, Google Workspace, Google Maps panels.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { DollarSign, RefreshCw, Loader2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from '@/components/ui/accordion';
import { useApiKeys, type APIUsageSummary } from '../../../hooks/useApiKeys';

interface Props {
  service: string;
  serviceName: string;
}

const ApiUsageSection: React.FC<Props> = ({ service, serviceName }) => {
  const { getAPIUsageSummary, isConnected } = useApiKeys();
  const [data, setData] = useState<APIUsageSummary | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!isConnected) return;
    setLoading(true);
    try {
      const services = await getAPIUsageSummary(service);
      setData(services.find((s) => s.service === service) ?? null);
    } finally {
      setLoading(false);
    }
  }, [isConnected, service, getAPIUsageSummary]);

  useEffect(() => { load(); }, [load]);

  const costBadge = data ? (
    <Badge variant="success">${data.total_cost.toFixed(4)}</Badge>
  ) : null;

  return (
    <Accordion type="single" collapsible>
      <AccordionItem value="usage">
        <AccordionTrigger>
          <span className="flex items-center gap-2">
            <DollarSign className="h-4 w-4 text-warning" />
            API Usage &amp; Costs {costBadge}
          </span>
        </AccordionTrigger>
        <AccordionContent>
          {loading ? (
            <div className="flex justify-center p-4">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          ) : !data ? (
            <Alert variant="info">
              <AlertDescription>
                No usage data yet. Use {serviceName} nodes in your workflows to track costs.
              </AlertDescription>
            </Alert>
          ) : (
            <div className="flex w-full flex-col gap-4">
              <div className="flex flex-wrap gap-4">
                <Stat label="Total Cost" value={`$${data.total_cost.toFixed(4)}`} className="text-success" />
                <Stat label="API Calls" value={data.execution_count} className="text-info" />
                <Stat label="Resources" value={data.total_resources} className="text-primary" />
              </div>

              {data.operations?.length > 0 && (
                <div className="overflow-hidden rounded-md border border-border">
                  <div className="border-b border-border bg-muted px-3 py-1.5 text-xs font-semibold">
                    Operations Breakdown
                  </div>
                  <div className="divide-y divide-border">
                    {data.operations.map((op) => (
                      <div
                        key={op.operation}
                        className="grid grid-cols-[1fr_auto] items-center gap-3 px-3 py-2 text-sm"
                      >
                        <code className="text-xs text-muted-foreground">{op.operation}</code>
                        <div className="flex items-center gap-1.5">
                          <Badge variant="outline">{op.resource_count} resources</Badge>
                          <Badge variant="success">${op.total_cost.toFixed(4)}</Badge>
                        </div>
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

export default ApiUsageSection;
