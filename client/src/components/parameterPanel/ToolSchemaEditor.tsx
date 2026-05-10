/**
 * ToolSchemaEditor - Schema editor for the Android Toolkit node.
 *
 * Lets the user customise the LLM-visible schema (description + fields)
 * for each connected Android service node. The form runs on RHF + zod
 * so per-field validation, dirty tracking, and the dynamic add/remove
 * row workflow come from the library instead of hand-rolled state.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Node } from 'reactflow';
import { useForm, useFieldArray, useFormContext } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useToolSchema, ToolSchemaConfig } from '../../hooks/useToolSchema';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { useAppStore } from '../../store/useAppStore';
import { isNodeInBackendGroup } from '../../lib/nodeSpec';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ActionButton } from '@/components/ui/action-button';
import { Checkbox } from '@/components/ui/checkbox';

import { resolveNodeDescription } from '../../lib/nodeSpec';
interface ToolSchemaEditorProps {
  nodeId: string;
  toolName: string;
  toolDescription: string;
}

// ---------------------------------------------------------------------------
// Schema (one source of truth for type, defaults, validation).
// ---------------------------------------------------------------------------

const FIELD_TYPES = ['string', 'number', 'integer', 'boolean', 'object', 'array'] as const;

const schemaFieldSchema = z.object({
  name: z
    .string()
    .min(1, 'Required')
    .regex(/^[a-zA-Z_][a-zA-Z0-9_]*$/, 'Letters / digits / underscore only'),
  type: z.enum(FIELD_TYPES),
  description: z.string().default(''),
  required: z.boolean().default(false),
});
type SchemaField = z.infer<typeof schemaFieldSchema>;

const toolSchemaFormSchema = z.object({
  description: z.string().default(''),
  fields: z.array(schemaFieldSchema),
});
type ToolSchemaFormValues = z.infer<typeof toolSchemaFormSchema>;

// Default schema for androidTool (matches the legacy DEFAULT_SCHEMA).
const DEFAULT_FORM_VALUES: ToolSchemaFormValues = {
  description: 'Control Android device via connected services',
  fields: [
    { name: 'service_id', type: 'string',
      description: 'Service to use (determined by connected Android nodes)',
      required: true },
    { name: 'action', type: 'string',
      description: 'Action to perform (see service list for available actions)',
      required: true },
    { name: 'parameters', type: 'object',
      description: 'Action parameters. Examples: {package_name: "com.app"} for app_launcher',
      required: false },
  ],
};

function defaultFormForService(service: Node | null): ToolSchemaFormValues {
  if (!service) return DEFAULT_FORM_VALUES;
  const serviceName = service.data?.label || service.type || 'service';
  return {
    description: `Control ${serviceName} on Android device`,
    fields: [
      { name: 'action', type: 'string',
        description: `Action to perform on ${serviceName}`, required: true },
      { name: 'parameters', type: 'object',
        description: `Parameters for the ${serviceName} action`, required: false },
    ],
  };
}

function configToFormValues(cfg: ToolSchemaConfig): ToolSchemaFormValues {
  return {
    description: cfg.description ?? '',
    fields: Object.entries(cfg.fields ?? {}).map(([name, c]) => ({
      name,
      type: (FIELD_TYPES as readonly string[]).includes(c.type as string)
        ? (c.type as SchemaField['type'])
        : 'string',
      description: c.description ?? '',
      required: !!c.required,
    })),
  };
}

function formValuesToConfig(values: ToolSchemaFormValues): ToolSchemaConfig {
  const fields: ToolSchemaConfig['fields'] = {};
  for (const f of values.fields) {
    fields[f.name] = { type: f.type, description: f.description, required: f.required };
  }
  return { description: values.description, fields };
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

const ToolSchemaEditor: React.FC<ToolSchemaEditorProps> = ({ nodeId }) => {
  const { getToolSchema, saveToolSchema, deleteToolSchema, isLoading } = useToolSchema();
  const { isConnected } = useWebSocket();
  const currentWorkflow = useAppStore((s) => s.currentWorkflow);

  const currentNode = useMemo(() => {
    if (!currentWorkflow?.nodes) return null;
    return currentWorkflow.nodes.find((n) => n.id === nodeId);
  }, [currentWorkflow?.nodes, nodeId]);

  // Wave 10.G.5: render only for nodes whose spec declares
  // `uiHints.isAndroidToolkit`. The androidTool node registers that
  // hint; no frontend type-string fallback.
  const currentNodeDef = currentNode?.type ? resolveNodeDescription(currentNode.type) : undefined;
  const isAndroidTool = (currentNodeDef?.uiHints as any)?.isAndroidToolkit === true;
  if (!isAndroidTool) return null;

  const connectedServices = useMemo(() => {
    if (!currentWorkflow?.edges || !currentWorkflow?.nodes) return [];
    const incomingEdges = currentWorkflow.edges.filter((edge) => edge.target === nodeId);
    const services: Node[] = [];
    for (const edge of incomingEdges) {
      const sourceNode = currentWorkflow.nodes.find((n) => n.id === edge.source);
      // Wave 10.G.5: backend `group` membership only. NodeSpec prefetch
      // runs on WS connect, so the cache is always warm by the time the
      // parameter panel mounts.
      const isAndroid = sourceNode
        ? (isNodeInBackendGroup(sourceNode.type, 'android') === true)
        : false;
      if (sourceNode && isAndroid) {
        services.push(sourceNode);
      }
    }
    return services;
  }, [currentWorkflow?.edges, currentWorkflow?.nodes, nodeId]);

  const [selectedServiceId, setSelectedServiceId] = useState<string | null>(null);
  const [isExpanded, setIsExpanded] = useState(true);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');

  // Auto-select the first connected service; reset if the current selection
  // is no longer connected.
  useEffect(() => {
    if (connectedServices.length === 0) {
      setSelectedServiceId(null);
      return;
    }
    if (!selectedServiceId || !connectedServices.find((s) => s.id === selectedServiceId)) {
      setSelectedServiceId(connectedServices[0].id);
    }
  }, [connectedServices, selectedServiceId]);

  const selectedService = useMemo(
    () => connectedServices.find((s) => s.id === selectedServiceId) ?? null,
    [connectedServices, selectedServiceId],
  );

  const form = useForm({
    resolver: zodResolver(toolSchemaFormSchema),
    defaultValues: DEFAULT_FORM_VALUES,
    mode: 'onChange',
  });
  const { fields, append, remove } = useFieldArray({ control: form.control, name: 'fields' });

  // Reload form whenever the selected service changes.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!isConnected || !selectedServiceId) {
        form.reset(DEFAULT_FORM_VALUES);
        return;
      }
      const stored = await getToolSchema(selectedServiceId);
      if (cancelled) return;
      if (stored?.schema_config && Object.keys(stored.schema_config.fields || {}).length > 0) {
        form.reset(configToFormValues(stored.schema_config));
      } else {
        form.reset(defaultFormForService(selectedService));
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedServiceId, isConnected]);

  const onSubmit = async (values: ToolSchemaFormValues) => {
    if (!selectedServiceId || !selectedService) return;
    setSaveStatus('saving');
    const serviceName = selectedService.data?.label || selectedService.type || 'unknown';
    const config = formValuesToConfig(values);
    const ok = await saveToolSchema(selectedServiceId, serviceName, config.description, config);
    setSaveStatus(ok ? 'saved' : 'error');
    if (ok) form.reset(values); // clears dirty state without losing values
    setTimeout(() => setSaveStatus('idle'), 2000);
  };

  const handleReset = async () => {
    if (!selectedServiceId) return;
    await deleteToolSchema(selectedServiceId);
    form.reset(defaultFormForService(selectedService));
  };

  const hasChanges = form.formState.isDirty;

  return (
    <div className="overflow-hidden rounded-md border border-border bg-background">
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className={cn(
          'flex cursor-pointer items-center justify-between bg-muted p-3',
          isExpanded && 'border-b border-border'
        )}
      >
        <div className="flex items-center gap-2">
          <ChevronDown
            className={cn(
              'h-3 w-3 text-muted-foreground transition-transform',
              !isExpanded && '-rotate-90'
            )}
          />
          <span className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-sm font-semibold text-fg-default">Connected Services</span>
        </div>
        <span className="text-sm text-muted-foreground">
          {connectedServices.length} service(s)
        </span>
      </div>

      {isExpanded && (
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="p-3">
            {connectedServices.length > 0 ? (
              <div className="mb-3">
                <label className="mb-1 block text-sm text-muted-foreground">Select Service</label>
                <Select
                  value={selectedServiceId ?? undefined}
                  onValueChange={(v) => setSelectedServiceId(v)}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Choose a connected service" />
                  </SelectTrigger>
                  <SelectContent>
                    {connectedServices.map((service) => (
                      <SelectItem key={service.id} value={service.id}>
                        {service.data?.label || service.type}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedService && (
                  <div className="mt-1 text-xs text-muted-foreground">
                    Type: {selectedService.type}
                  </div>
                )}
              </div>
            ) : (
              <div className="mb-3 rounded border border-warning/30 bg-warning/10 p-2 text-sm text-warning">
                Connect Android nodes to the input handle
              </div>
            )}

            <div className="mb-2 flex items-center justify-between">
              <label className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-sm text-fg-muted">Schema Fields</label>
              <ActionButton
                intent="tools"
                className="h-7"
                onClick={() =>
                  append({
                    name: `field_${fields.length + 1}`,
                    type: 'string',
                    description: '',
                    required: false,
                  })
                }
              >
                + Add
              </ActionButton>
            </div>

            <div className="flex flex-col gap-1">
              {fields.map((field, index) => (
                <FieldRow key={field.id} index={index} onRemove={() => remove(index)} />
              ))}
            </div>

            {hasChanges && (
              <div className="mt-3 flex justify-end gap-2">
                <ActionButton intent="config" type="button" onClick={handleReset}>
                  Reset
                </ActionButton>
                <ActionButton
                  intent="save"
                  type="submit"
                  disabled={isLoading || saveStatus === 'saving'}
                >
                  {saveStatus === 'saving' ? 'Saving...' : saveStatus === 'saved' ? 'Saved!' : 'Save'}
                </ActionButton>
              </div>
            )}
          </form>
        </Form>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Field row — connected to RHF via parent <Form/>; uses useFormContext.
// ---------------------------------------------------------------------------

const FieldRow: React.FC<{ index: number; onRemove: () => void }> = ({ index, onRemove }) => {
  const { control } = useFormContext<ToolSchemaFormValues>();
  return (
    <div className="rounded border border-border bg-card p-2">
      <div className="mb-1 flex items-center gap-1">
        <FormField
          control={control}
          name={`fields.${index}.name`}
          render={({ field }) => (
            <FormItem className="flex-1">
              <FormControl>
                <Input
                  {...field}
                  className="h-8"
                  onChange={(e) => field.onChange(e.target.value.replace(/\s/g, '_'))}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name={`fields.${index}.type`}
          render={({ field }) => (
            <FormItem>
              <Select onValueChange={field.onChange} value={field.value}>
                <FormControl>
                  <SelectTrigger className="h-8 w-[110px] text-xs">
                    <SelectValue />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {FIELD_TYPES.map((t) => (
                    <SelectItem key={t} value={t}>
                      {t}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name={`fields.${index}.required`}
          render={({ field }) => (
            <FormItem className="flex items-center gap-1 space-y-0 px-1 text-xs text-muted-foreground">
              <FormControl>
                <Checkbox checked={field.value} onCheckedChange={field.onChange} />
              </FormControl>
              <span>Req</span>
            </FormItem>
          )}
        />
        <ActionButton
          intent="stop"
          onClick={onRemove}
          className="h-7 px-2 text-xs"
        >
          X
        </ActionButton>
      </div>
      <FormField
        control={control}
        name={`fields.${index}.description`}
        render={({ field }) => (
          <FormItem>
            <FormControl>
              <Input {...field} placeholder="Description..." className="h-7 text-xs" />
            </FormControl>
          </FormItem>
        )}
      />
    </div>
  );
};

export default ToolSchemaEditor;
