/**
 * PricingConfigModal - View and edit pricing configuration
 * Displays LLM and API pricing in a tree view with inline editing
 */

import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, RefreshCw, Save, DollarSign } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from '@/components/ui/accordion';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { toast } from 'sonner';
import Modal from './ui/Modal';
import { ActionButton } from './ui/action-button';
import { usePricing, PricingConfig, LLMPricing } from '../hooks/usePricing';

// ============================================================================
// TYPES
// ============================================================================

interface Props {
  visible: boolean;
  onClose: () => void;
}

// ============================================================================
// HELPER COMPONENTS
// ============================================================================

interface MoneyInputProps {
  value: number | undefined;
  onChange: (v: number) => void;
  widthClass?: string;
  min?: number;
  step?: number;
}

const MoneyInput: React.FC<MoneyInputProps> = ({ value, onChange, widthClass = 'w-20', min = 0, step = 0.01 }) => (
  <div className={`relative ${widthClass}`}>
    <span className="pointer-events-none absolute top-1/2 left-2 -translate-y-1/2 text-xs text-muted-foreground">
      $
    </span>
    <Input
      type="number"
      min={min}
      step={step}
      value={value ?? ''}
      onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
      className="h-7 pl-5 pr-1 text-xs"
    />
  </div>
);

interface LLMModelRowProps {
  model: string;
  pricing: LLMPricing;
  onChange: (field: keyof LLMPricing, value: number) => void;
}

const LLMModelRow: React.FC<LLMModelRowProps> = ({ model, pricing, onChange }) => (
  <div
    className={`flex items-center gap-3 border-b border-border px-3 py-2 ${
      model === '_default' ? 'bg-node-agent-soft' : ''
    }`}
  >
    <div
      className={`flex-1 font-mono text-[13px] ${
        model === '_default' ? 'text-node-agent' : 'text-foreground'
      }`}
    >
      {model === '_default' ? 'Default' : model}
    </div>
    <div className="flex items-center gap-2">
      <span className="text-[11px] text-muted-foreground">Input:</span>
      <MoneyInput value={pricing.input} onChange={(v) => onChange('input', v)} />
      <span className="text-[11px] text-muted-foreground">Output:</span>
      <MoneyInput value={pricing.output} onChange={(v) => onChange('output', v)} />
      {pricing.cache_read !== undefined && (
        <>
          <span className="text-[11px] text-muted-foreground">Cache:</span>
          <MoneyInput value={pricing.cache_read} onChange={(v) => onChange('cache_read', v)} widthClass="w-[72px]" />
        </>
      )}
      {pricing.reasoning !== undefined && (
        <>
          <span className="text-[11px] text-muted-foreground">Reasoning:</span>
          <MoneyInput value={pricing.reasoning} onChange={(v) => onChange('reasoning', v)} />
        </>
      )}
    </div>
  </div>
);

interface APIPricingRowProps {
  operation: string;
  price: number;
  onChange: (value: number) => void;
}

const APIPricingRow: React.FC<APIPricingRowProps> = ({ operation, price, onChange }) => {
  // Skip metadata keys
  if (operation.startsWith('_')) return null;

  return (
    <div className="flex items-center justify-between border-b border-border px-3 py-2">
      <div className="font-mono text-[13px] text-foreground">
        {operation}
      </div>
      <MoneyInput value={price} onChange={onChange} widthClass="w-24" step={0.001} />
    </div>
  );
};

// ============================================================================
// MAIN COMPONENT
// ============================================================================

const PricingConfigModal: React.FC<Props> = ({ visible, onClose }) => {
  const { getPricingConfig, savePricingConfig, isConnected } = usePricing();

  const [config, setConfig] = useState<PricingConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [isDirty, setIsDirty] = useState(false);

  // Load config on mount
  const loadConfig = useCallback(async () => {
    if (!isConnected) return;
    setLoading(true);
    try {
      const data = await getPricingConfig();
      setConfig(data);
      setIsDirty(false);
    } catch (error) {
      toast.error('Failed to load pricing config');
    } finally {
      setLoading(false);
    }
  }, [getPricingConfig, isConnected]);

  useEffect(() => {
    if (visible) {
      loadConfig();
    }
  }, [visible, loadConfig]);

  // Save config
  const handleSave = useCallback(async () => {
    if (!config) return;
    setSaving(true);
    try {
      const success = await savePricingConfig(config);
      if (success) {
        toast.success('Pricing config saved');
        setIsDirty(false);
      } else {
        toast.error('Failed to save pricing config');
      }
    } catch (error) {
      toast.error('Failed to save pricing config');
    } finally {
      setSaving(false);
    }
  }, [config, savePricingConfig]);

  // Update LLM pricing
  const updateLLMPricing = useCallback((
    provider: string,
    model: string,
    field: keyof LLMPricing,
    value: number
  ) => {
    if (!config) return;
    setConfig(prev => {
      if (!prev) return prev;
      const updated = { ...prev };
      if (!updated.llm[provider]) updated.llm[provider] = {};
      if (!updated.llm[provider][model]) {
        updated.llm[provider][model] = { input: 0, output: 0 };
      }
      updated.llm[provider][model] = { ...updated.llm[provider][model], [field]: value };
      return updated;
    });
    setIsDirty(true);
  }, [config]);

  // Update API pricing
  const updateAPIPricing = useCallback((
    service: string,
    operation: string,
    value: number
  ) => {
    if (!config) return;
    setConfig(prev => {
      if (!prev) return prev;
      const updated = { ...prev };
      if (!updated.api[service]) updated.api[service] = {};
      (updated.api[service] as Record<string, number>)[operation] = value;
      return updated;
    });
    setIsDirty(true);
  }, [config]);

  // Render LLM tab
  const renderLLMTab = () => {
    if (!config?.llm) return null;

    const providers = Object.keys(config.llm).sort();

    return (
      <Accordion type="multiple" defaultValue={providers.slice(0, 2)} className="bg-transparent">
        {providers.map((provider) => {
          const models = config.llm[provider];
          const modelNames = Object.keys(models).sort((a, b) => {
            if (a === '_default') return 1;
            if (b === '_default') return -1;
            return a.localeCompare(b);
          });

          return (
            <AccordionItem key={provider} value={provider}>
              <AccordionTrigger>
                <span className="font-medium capitalize">
                  {provider}
                  <span className="ml-2 text-xs text-muted-foreground">
                    ({modelNames.length} models)
                  </span>
                </span>
              </AccordionTrigger>
              <AccordionContent>
                <div className="px-3 py-1 text-[11px] text-muted-foreground">
                  Prices in USD per million tokens (MTok)
                </div>
                {modelNames.map((model) => (
                  <LLMModelRow
                    key={model}
                    model={model}
                    pricing={models[model]}
                    onChange={(field, value) => updateLLMPricing(provider, model, field, value)}
                  />
                ))}
              </AccordionContent>
            </AccordionItem>
          );
        })}
      </Accordion>
    );
  };

  // Render API tab
  const renderAPITab = () => {
    if (!config?.api) return null;

    const services = Object.keys(config.api).sort();

    return (
      <Accordion type="multiple" defaultValue={services} className="bg-transparent">
        {services.map((service) => {
          const operations = config.api[service];
          const opNames = Object.keys(operations).filter((k) => !k.startsWith('_')).sort();
          const description = (operations as Record<string, string | number>)._description as string | undefined;
          const source = (operations as Record<string, string | number>)._source as string | undefined;

          return (
            <AccordionItem key={service} value={service}>
              <AccordionTrigger>
                <span className="font-medium capitalize">
                  {service}
                  <span className="ml-2 text-xs text-muted-foreground">
                    ({opNames.length} operations)
                  </span>
                </span>
              </AccordionTrigger>
              <AccordionContent>
                {description && (
                  <div className="px-3 py-1 pb-2 text-xs text-muted-foreground">
                    {description}
                    {source && (
                      <a
                        href={source}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="ml-2 text-info"
                      >
                        Source
                      </a>
                    )}
                  </div>
                )}
                <div className="px-3 py-1 text-[11px] text-muted-foreground">
                  Prices in USD per resource/request
                </div>
                {opNames.map((operation) => (
                  <APIPricingRow
                    key={operation}
                    operation={operation}
                    price={(operations as Record<string, number>)[operation]}
                    onChange={(value) => updateAPIPricing(service, operation, value)}
                  />
                ))}
              </AccordionContent>
            </AccordionItem>
          );
        })}
      </Accordion>
    );
  };

  return (
    <Modal
      isOpen={visible}
      onClose={onClose}
      title="Pricing Configuration"
      maxWidth="700px"
      maxHeight="85vh"
      headerActions={
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={loadConfig} disabled={loading}>
            {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
            Reload
          </Button>
          <ActionButton intent="save" onClick={handleSave} disabled={!isDirty || saving}>
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            Save
          </ActionButton>
        </div>
      }
    >
      <div className="p-4">
        {loading ? (
          <div className="flex items-center justify-center p-10">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : config ? (
          <>
            {/* Version info */}
            <div className="mb-4 flex items-center justify-between rounded-md bg-bg-panel px-3 py-2">
              <div className="flex items-center gap-2">
                <DollarSign className="h-4 w-4 text-warning" />
                <span className="text-muted-foreground">
                  Version: <strong className="text-foreground">{config.version}</strong>
                </span>
              </div>
              <span className="text-xs text-muted-foreground">
                Last updated: {config.last_updated}
              </span>
            </div>

            {/* Tabs */}
            <Tabs defaultValue="llm">
              <TabsList>
                <TabsTrigger value="llm">LLM Pricing</TabsTrigger>
                <TabsTrigger value="api">API Pricing</TabsTrigger>
              </TabsList>
              <TabsContent value="llm">{renderLLMTab()}</TabsContent>
              <TabsContent value="api">{renderAPITab()}</TabsContent>
            </Tabs>
          </>
        ) : (
          <div className="p-10 text-center text-muted-foreground">
            {isConnected ? 'Failed to load config' : 'Not connected'}
          </div>
        )}
      </div>
    </Modal>
  );
};

export default PricingConfigModal;
