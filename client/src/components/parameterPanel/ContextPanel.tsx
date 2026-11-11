import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import JsonView from '@uiw/react-json-view';
import { Loader2, RefreshCw, ShieldCheck, Trash2, Wrench } from 'lucide-react';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useWebSocket } from '@/contexts/WebSocketContext';
import { cn } from '@/lib/utils';
import { formatTimestamp, tryParseJson } from '@/utils/formatters';

interface ToolCallView {
  id?: string;
  name?: string;
  args?: unknown;
  raw_arguments?: string;
  parse_error?: string;
}

interface ConversationMessage {
  role?: string;
  content?: unknown;
  tool_calls?: ToolCallView[];
  tool_call_id?: string;
  name?: string;
  /** Stamped by the store when the message first persisted. */
  ts?: string;
}

interface ConversationMeta {
  workflow_id: string;
  generation: number;
  agent_node_id: string;
  message_count: number;
  updated_at?: string | null;
}

interface ContextSnapshot {
  conversations?: ConversationMeta[];
  generation?: number | null;
  agent_node_id?: string | null;
  updated_at?: string | null;
  message_count?: number;
  messages?: ConversationMessage[];
}

interface ContextResponse {
  success?: boolean;
  context?: ContextSnapshot;
  error?: string;
}

interface ContextPanelProps {
  nodeId: string;
  workflowId?: string;
}

const contextQueryKey = (
  workflowId: string | undefined,
  nodeId: string,
  agent: string,
) => ['agentContext', workflowId ?? '', nodeId, agent] as const;

/** Per-role card treatment — same tokens the chat surface uses
 * (ConsolePanel: user bubbles on `bg-node-agent-soft`, bot bubbles on
 * `bg-bg-elevated`), so every theme's palette carries over. */
const ROLE_CARD: Record<string, string> = {
  user: 'bg-node-agent-soft border-border',
  assistant: 'bg-bg-elevated border-border-default',
  tool: 'bg-card border-border',
  system: 'bg-card border-border',
};

/** Route a value to the themed JSON tree when it is (or parses to) an
 * object; fall back to readable text. Tool payloads arrive as serialized
 * JSON strings, which read as noise without the tree. The tree paints on
 * the per-theme `--code-*` surface via the global `--w-rjv-*` mapping. */
const JsonOrText: React.FC<{
  value: unknown;
  collapsed?: number;
  mono?: boolean;
}> = ({ value, collapsed = 2, mono = false }) => {
  if (typeof value === 'string') {
    const parsed = tryParseJson(value);
    if (parsed) {
      return (
        <div className="overflow-x-auto rounded-md border border-[var(--code-border)] bg-[var(--code-bg)] p-2">
          <JsonView value={parsed} collapsed={collapsed} displayDataTypes={false} />
        </div>
      );
    }
    return (
      <div
        className={cn(
          'text-sm whitespace-pre-wrap',
          mono
            ? 'font-mono rounded-md border border-[var(--code-border)] bg-[var(--code-bg)] p-2 text-[var(--code-text)]'
            : 'text-foreground',
        )}
      >
        {value}
      </div>
    );
  }
  if (value != null && typeof value === 'object') {
    return (
      <div className="overflow-x-auto rounded-md border border-[var(--code-border)] bg-[var(--code-bg)] p-2">
        <JsonView
          value={value as object}
          collapsed={collapsed}
          displayDataTypes={false}
        />
      </div>
    );
  }
  if (value == null) return null;
  return (
    <div className="text-sm whitespace-pre-wrap text-foreground">
      {String(value)}
    </div>
  );
};

/** Authorized, query-backed viewer for the agent's live conversation.
 *
 * Shows the CURRENT context only — the newest workflow generation, exactly
 * what the agent loads on its next firing. It never reads transcript data
 * from workflow params, node status, or websocket broadcasts —
 * `context.updated` broadcasts only trigger a refetch through the
 * authorized `get_agent_context` handler.
 */
const ContextPanel: React.FC<ContextPanelProps> = ({ nodeId, workflowId }) => {
  const { sendRequest } = useWebSocket();
  const queryClient = useQueryClient();
  // '' = server default (the newest stored conversation). Only meaningful
  // when several agents share this Context node.
  const [selectedAgent, setSelectedAgent] = useState<string>('');
  // 'formatted' renders role cards; 'raw' shows the stored wire messages
  // verbatim — the exact JSON the agent's next firing loads.
  const [view, setView] = useState<'formatted' | 'raw'>('formatted');

  const queryKey = contextQueryKey(workflowId, nodeId, selectedAgent);
  const contextQuery = useQuery<ContextResponse, Error>({
    queryKey,
    queryFn: () =>
      sendRequest<ContextResponse>('get_agent_context', {
        workflow_id: workflowId,
        context_node_id: nodeId,
        ...(selectedAgent ? { agent_node_id: selectedAgent } : {}),
      }),
    enabled: !!workflowId && !!nodeId,
    // The global default is refetchOnMount: false ("trust the cache; the
    // broadcast bridge keeps it in sync"), but a conversation grows while
    // this panel is CLOSED — the `context.updated` invalidation has no
    // active observer then, so a remount must always fetch fresh or the
    // panel opens on stale data until a manual Refresh.
    refetchOnMount: 'always',
    staleTime: 0,
  });
  const snapshot = contextQuery.data?.context ?? {};
  const conversations = snapshot.conversations ?? [];
  const messages = snapshot.messages ?? [];

  const invalidateContext = async () => {
    await queryClient.invalidateQueries({
      queryKey: ['agentContext', workflowId ?? '', nodeId],
    });
  };
  const clearMutation = useMutation({
    mutationFn: () =>
      sendRequest('clear_agent_context', {
        workflow_id: workflowId,
        context_node_id: nodeId,
        ...(snapshot.generation != null ? { generation: snapshot.generation } : {}),
        ...(snapshot.agent_node_id ? { agent_node_id: snapshot.agent_node_id } : {}),
      }),
    onSuccess: async () => {
      setSelectedAgent('');
      await invalidateContext();
      toast.success('Conversation cleared');
    },
    onError: () => toast.error('Failed to clear conversation'),
  });

  if (!workflowId) {
    return (
      <div className="p-6">
        {/* A hint, not a state, so neutral. */}
        <Alert className="border-border-default bg-bg-panel text-fg-default">
          <ShieldCheck />
          <AlertTitle>Context is workflow-scoped</AlertTitle>
          <AlertDescription>Save the workflow before inspecting its Context.</AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-base font-semibold text-fg-default">
            Agent Conversation
          </h3>
          <p className="text-xs text-muted-foreground">
            The connected agent's live context — exactly what it loads on its next turn.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Tabs
            value={view}
            onValueChange={(next) => setView(next as 'formatted' | 'raw')}
          >
            <TabsList>
              <TabsTrigger value="formatted">Formatted</TabsTrigger>
              <TabsTrigger value="raw">Raw</TabsTrigger>
            </TabsList>
          </Tabs>
          <Button
            variant="quiet"
            size="sm"
            className="border-border-default text-fg-default"
            onClick={() => void contextQuery.refetch()}
            disabled={contextQuery.isFetching}
          >
            <RefreshCw className={contextQuery.isFetching ? 'animate-spin' : ''} />
            Refresh
          </Button>
          <Button
            variant="quiet"
            size="sm"
            className="border-border-default text-fg-default"
            onClick={() => clearMutation.mutate()}
            disabled={clearMutation.isPending || messages.length === 0}
          >
            <Trash2 />
            Clear
          </Button>
        </div>
      </div>

      {conversations.length > 1 && (
        <Select
          value={selectedAgent || (snapshot.agent_node_id ?? '')}
          onValueChange={setSelectedAgent}
        >
          <SelectTrigger className="w-full max-w-md">
            <SelectValue placeholder="Select an agent" />
          </SelectTrigger>
          <SelectContent>
            {conversations.map((meta) => (
              <SelectItem key={meta.agent_node_id} value={meta.agent_node_id}>
                {meta.agent_node_id} · {meta.message_count} message
                {meta.message_count === 1 ? '' : 's'}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {snapshot.agent_node_id && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="outline">{snapshot.agent_node_id}</Badge>
          <span>
            {snapshot.message_count ?? 0} message
            {(snapshot.message_count ?? 0) === 1 ? '' : 's'}
          </span>
          {snapshot.updated_at && (
            <span className="font-mono text-[11px] tabular-nums">
              updated {formatTimestamp(snapshot.updated_at)}
            </span>
          )}
        </div>
      )}

      {contextQuery.isLoading ? (
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      ) : contextQuery.error ? (
        <Alert variant="destructive">
          <AlertTitle>Context unavailable</AlertTitle>
          <AlertDescription>{contextQuery.error.message}</AlertDescription>
        </Alert>
      ) : messages.length === 0 ? (
        <div className="rounded-md border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          No stored conversation yet.
        </div>
      ) : view === 'raw' ? (
        // The stored wire messages verbatim, on the per-theme code surface —
        // the exact JSON the agent's next firing loads.
        <div className="overflow-x-auto rounded-md border border-[var(--code-border)] bg-[var(--code-bg)] p-2">
          <JsonView value={messages} collapsed={2} displayDataTypes={false} />
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {messages.map((message, index) => (
            <div
              key={index}
              className={cn(
                'rounded-md border p-3',
                ROLE_CARD[message.role || ''] || 'bg-card border-border',
              )}
            >
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <Badge variant="outline">{message.role || 'message'}</Badge>
                {message.name && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {message.name}
                  </span>
                )}
                {message.ts && (
                  <span className="ml-auto font-mono text-[11px] tabular-nums text-muted-foreground">
                    {formatTimestamp(message.ts)}
                  </span>
                )}
              </div>
              <JsonOrText value={message.content} mono={message.role === 'tool'} />
              {(message.tool_calls?.length ?? 0) > 0 && (
                <div className="mt-2 flex flex-col gap-2">
                  {message.tool_calls!.map((call, callIndex) => (
                    <div
                      key={call.id || callIndex}
                      className="rounded-md border border-[var(--code-border)] bg-[var(--code-bg)] p-2"
                    >
                      <div className="mb-1 flex flex-wrap items-center gap-2">
                        <Wrench className="h-3.5 w-3.5 text-[var(--code-comment)]" />
                        <span className="font-mono text-xs font-medium text-[var(--code-text)]">
                          {call.name || 'tool call'}
                        </span>
                        {call.parse_error && (
                          <Badge variant="warning">invalid arguments</Badge>
                        )}
                      </div>
                      <JsonOrText
                        value={
                          call.args && Object.keys(call.args as object).length > 0
                            ? call.args
                            : call.raw_arguments
                        }
                        collapsed={1}
                        mono
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default ContextPanel;
