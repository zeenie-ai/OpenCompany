import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ChevronLeft,
  ChevronRight,
  Database,
  Loader2,
  Plus,
  Save,
  Search,
  Trash2,
} from 'lucide-react';
import { toast } from 'sonner';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useWebSocket } from '@/contexts/WebSocketContext';

const PAGE_SIZE = 25;

interface MemoryItemView {
  /** Store views use `id`; API adapters may expose the public `memory_id`. */
  id?: string;
  memory_id?: string;
  version: number;
  title?: string | null;
  content: string;
  category?: string | null;
  tags?: string[];
  expires_at?: string | null;
  created_at?: string;
  updated_at?: string;
  indexing_state?: string;
}

interface MemoryListResponse {
  success?: boolean;
  items?: MemoryItemView[];
  next_cursor?: string | null;
  indexing_state?: string;
  error?: string;
}

interface MemoryItemResponse {
  success?: boolean;
  item?: MemoryItemView;
  memory?: MemoryItemView;
  error?: string;
}

interface MemoryToolPanelProps {
  nodeId: string;
  workflowId?: string;
  parameters: Record<string, unknown>;
  onParameterChange: (name: string, value: unknown) => void;
}

interface MemoryDraft {
  title: string;
  content: string;
  category: string;
  tags: string;
}

const EMPTY_DRAFT: MemoryDraft = {
  title: '',
  content: '',
  category: '',
  tags: '',
};

const memoryListPrefix = (workflowId: string | undefined, nodeId: string) =>
  ['memoryItems', workflowId ?? '', nodeId] as const;

const memoryItemId = (item: MemoryItemView | undefined): string =>
  item?.memory_id || item?.id || '';

const MemoryToolPanel: React.FC<MemoryToolPanelProps> = ({
  nodeId,
  workflowId,
  parameters,
  onParameterChange,
}) => {
  const { sendRequest } = useWebSocket();
  const queryClient = useQueryClient();
  const [searchInput, setSearchInput] = useState('');
  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<MemoryDraft>(EMPTY_DRAFT);
  const [showClearDialog, setShowClearDialog] = useState(false);

  const listPrefix = memoryListPrefix(workflowId, nodeId);
  const listQuery = useQuery<MemoryListResponse, Error>({
    queryKey: [...listPrefix, query, cursor ?? ''],
    queryFn: () =>
      sendRequest<MemoryListResponse>('list_memory_items', {
        workflow_id: workflowId,
        memory_node_id: nodeId,
        query: query || undefined,
        cursor,
        limit: PAGE_SIZE,
      }),
    enabled: !!workflowId && !!nodeId,
    // Same contract as ContextPanel: the agent mutates memory while this
    // panel is CLOSED, so the `memory.updated` invalidation has no active
    // observer then — a remount must always fetch fresh.
    refetchOnMount: 'always',
    staleTime: 0,
  });
  const itemQuery = useQuery<MemoryItemResponse, Error>({
    queryKey: ['memoryItem', workflowId ?? '', nodeId, selectedId ?? 'new'],
    queryFn: () =>
      sendRequest<MemoryItemResponse>('get_memory_item', {
        workflow_id: workflowId,
        memory_node_id: nodeId,
        memory_id: selectedId,
      }),
    enabled: !!workflowId && !!selectedId,
    refetchOnMount: 'always',
    staleTime: 0,
  });

  const selected = itemQuery.data?.item ?? itemQuery.data?.memory;
  useEffect(() => {
    if (!selected) return;
    setDraft({
      title: selected.title || '',
      content: selected.content,
      category: selected.category || '',
      tags: (selected.tags || []).join(', '),
    });
  }, [selected]);

  const invalidateItems = async () => {
    await queryClient.invalidateQueries({ queryKey: listPrefix });
    if (selectedId) {
      await queryClient.invalidateQueries({
        queryKey: ['memoryItem', workflowId ?? '', nodeId, selectedId],
      });
    }
  };
  const rememberMutation = useMutation({
    mutationFn: () =>
      sendRequest<MemoryItemResponse>('remember_memory', {
        workflow_id: workflowId,
        memory_node_id: nodeId,
        content: draft.content,
        title: draft.title || undefined,
        category: draft.category || undefined,
        tags: draft.tags
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
      }),
    onSuccess: async (response) => {
      const remembered = response.item ?? response.memory;
      setSelectedId(memoryItemId(remembered) || null);
      await invalidateItems();
      toast.success('Memory saved');
    },
    onError: () => toast.error('Failed to save Memory'),
  });
  const updateMutation = useMutation({
    mutationFn: () =>
      sendRequest<MemoryItemResponse>('update_memory_item', {
        workflow_id: workflowId,
        memory_node_id: nodeId,
        memory_id: memoryItemId(selected),
        expected_version: selected?.version,
        patch: {
          title: draft.title || null,
          content: draft.content,
          category: draft.category || null,
          tags: draft.tags
            .split(',')
            .map((tag) => tag.trim())
            .filter(Boolean),
        },
      }),
    onSuccess: async () => {
      await invalidateItems();
      toast.success('Memory updated');
    },
    onError: () => toast.error('Memory changed elsewhere; refresh and retry'),
  });
  const forgetMutation = useMutation({
    mutationFn: () =>
      sendRequest('forget_memory_item', {
        workflow_id: workflowId,
        memory_node_id: nodeId,
        memory_id: memoryItemId(selected),
        expected_version: selected?.version,
      }),
    onSuccess: async () => {
      setSelectedId(null);
      setDraft(EMPTY_DRAFT);
      await invalidateItems();
      toast.success('Memory forgotten');
    },
    onError: () => toast.error('Failed to forget Memory'),
  });
  const clearMutation = useMutation({
    mutationFn: () =>
      sendRequest('clear_memory_items', {
        workflow_id: workflowId,
        memory_node_id: nodeId,
      }),
    onSuccess: async () => {
      setSelectedId(null);
      setDraft(EMPTY_DRAFT);
      setShowClearDialog(false);
      await invalidateItems();
      toast.success('All Memory items cleared');
    },
    onError: () => toast.error('Failed to clear Memory items'),
  });

  const items = listQuery.data?.items ?? [];
  const isSaving = rememberMutation.isPending || updateMutation.isPending;
  const canSave = draft.content.trim().length > 0 && !isSaving;

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[minmax(240px,0.8fr)_minmax(320px,1.2fr)]">
      <section className="flex min-h-0 flex-col gap-3 border-r border-border p-4">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="text-base font-semibold">Memory</h3>
            <p className="text-xs text-muted-foreground">
              Explicit durable items exposed through the <code>memory</code> tool.
            </p>
          </div>
          <Badge variant="outline">
            <Database />
            {listQuery.data?.indexing_state || 'lexical ready'}
          </Badge>
        </div>

        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-8"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  setQuery(searchInput.trim());
                  setCursor(null);
                  setCursorHistory([]);
                }
              }}
              placeholder="Search memories"
            />
          </div>
          <Button
            variant="quiet"
            size="icon"
            className="border-border-default text-fg-default"
            aria-label="Search Memory"
            onClick={() => {
              setQuery(searchInput.trim());
              setCursor(null);
              setCursorHistory([]);
            }}
          >
            <Search />
          </Button>
          <Button
            variant="quiet"
            size="icon"
            className="border-border-default text-fg-default"
            aria-label="New Memory"
            onClick={() => {
              setSelectedId(null);
              setDraft(EMPTY_DRAFT);
            }}
          >
            <Plus />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {listQuery.isLoading ? (
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          ) : items.length === 0 ? (
            <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
              No Memory items match this query.
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {items.map((item) => (
                <button
                  key={memoryItemId(item)}
                  type="button"
                  className={`row rounded-md border p-3 text-left ${
                    selectedId === memoryItemId(item)
                      ? 'border-border-strong bg-bg-active'
                      : 'border-border bg-card'
                  }`}
                  onClick={() => setSelectedId(memoryItemId(item))}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">
                      {item.title || item.content.slice(0, 60)}
                    </span>
                    <span className="text-[10px] text-muted-foreground">v{item.version}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                    {item.content}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {item.category && <Badge variant="secondary">{item.category}</Badge>}
                    {(item.tags || []).slice(0, 3).map((tag) => (
                      <Badge key={tag} variant="outline">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between">
          <Button
            variant="quiet"
            size="icon-sm"
            className="border-border-default text-fg-default"
            aria-label="Previous Memory page"
            disabled={cursorHistory.length === 0}
            onClick={() => {
              setCursorHistory((history) => {
                const next = [...history];
                setCursor(next.pop() ?? null);
                return next;
              });
            }}
          >
            <ChevronLeft />
          </Button>
          <span className="text-xs text-muted-foreground">{PAGE_SIZE} per page</span>
          <Button
            variant="quiet"
            size="icon-sm"
            className="border-border-default text-fg-default"
            aria-label="Next Memory page"
            disabled={!listQuery.data?.next_cursor}
            onClick={() => {
              if (!listQuery.data?.next_cursor) return;
              setCursorHistory((history) => [...history, cursor]);
              setCursor(listQuery.data.next_cursor);
            }}
          >
            <ChevronRight />
          </Button>
        </div>
      </section>

      <section className="min-h-0 overflow-y-auto p-4">
        {/* This panel replaces the generic parameter list, so it has to render
            `reset_policy` itself — the options below duplicate the backend
            Literal in `simple_memory/__init__.py` and must be kept in step
            with it. (`server_controlled_fields` marks the field so the MODEL
            cannot override it through tool arguments; the operator still
            sets it here.) */}
        <div className="mb-4 grid gap-3 rounded-md border border-border bg-card p-3 sm:grid-cols-2">
          <label className="text-xs font-medium text-muted-foreground">
            Workflow Reset policy
            <Select
              value={String(parameters.reset_policy || 'preserve')}
              onValueChange={(value) => onParameterChange('reset_policy', value)}
            >
              <SelectTrigger className="mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="preserve">Preserve items</SelectItem>
                <SelectItem value="clear">Clear items</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <div className="flex items-end justify-end">
            <Button
              variant="quiet"
              size="sm"
              className="border-border-default text-fg-default"
              onClick={() => setShowClearDialog(true)}
            >
              <Trash2 />
              Clear all items
            </Button>
          </div>
        </div>

        {selectedId && itemQuery.isLoading ? (
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between gap-2">
              <h4 className="font-medium">{selected ? 'Edit Memory item' : 'Remember something'}</h4>
              {selected && (
                <Badge variant="outline">
                  {/* 'indexed' is not a member of the backend enum
                      (lexical | embedding_ready | embedding_failed), so
                      defaulting to it rendered an embedding failure — or an
                      absent verdict — as success. */}
                  {selected.indexing_state || 'unknown'} · v{selected.version}
                </Badge>
              )}
            </div>
            <Input
              value={draft.title}
              onChange={(event) =>
                setDraft((current) => ({ ...current, title: event.target.value }))
              }
              placeholder="Title (optional)"
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <Input
                value={draft.category}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, category: event.target.value }))
                }
                placeholder="Category"
              />
              <Input
                value={draft.tags}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, tags: event.target.value }))
                }
                placeholder="Tags, comma separated"
              />
            </div>
            <Textarea
              rows={12}
              value={draft.content}
              onChange={(event) =>
                setDraft((current) => ({ ...current, content: event.target.value }))
              }
              placeholder="Durable fact, preference, decision, or note"
            />
            <div className="flex justify-end gap-2">
              {selected && (
                <Button
                  variant="quiet"
                  className="border-border-default text-fg-default"
                  onClick={() => forgetMutation.mutate()}
                  disabled={forgetMutation.isPending}
                >
                  <Trash2 />
                  Forget
                </Button>
              )}
              <Button
                variant="invert"
                onClick={() =>
                  selected ? updateMutation.mutate() : rememberMutation.mutate()
                }
                disabled={!canSave}
              >
                {isSaving ? <Loader2 className="animate-spin" /> : <Save />}
                {selected ? 'Update' : 'Remember'}
              </Button>
            </div>
          </div>
        )}
      </section>

      <AlertDialog open={showClearDialog} onOpenChange={setShowClearDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Clear all durable Memory items?</AlertDialogTitle>
            <AlertDialogDescription>
              This is a human-only bulk action. It does not clear Agent Context or Todos.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={clearMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={clearMutation.isPending}
              onClick={() => clearMutation.mutate()}
            >
              Clear Memory
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default MemoryToolPanel;
