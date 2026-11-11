import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  File,
  Folder,
  HardDrive,
  Home,
  Loader2,
  Plus,
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
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { useWebSocket } from '@/contexts/WebSocketContext';

const BROWSE_LIMIT = 100;

/**
 * The panel is UI only. Every decision — mount validation, containment,
 * listing rows, breadcrumbs, writability — is made by the backend handlers
 * (`data_list_mounts` / `data_add_mount` / `data_update_mount` /
 * `data_remove_mount` / `data_browse`); this component renders responses
 * verbatim and writes the node's `mounts` parameter.
 */

interface MountRow {
  name: string;
  root_path: string;
  writable: boolean;
}

interface MountsResponse {
  success?: boolean;
  mounts?: MountRow[];
  error?: string;
}

interface BrowseEntry {
  name: string;
  is_dir: boolean;
  size_bytes?: number;
  mime_type?: string | null;
  /** Workspace rows carry `path`; mount rows carry `location`. */
  path?: string;
  location?: string;
  writable?: boolean;
}

interface BrowseResponse {
  success?: boolean;
  source?: 'workspace' | 'mount';
  path?: string;
  crumbs?: Array<{ name: string; path: string }>;
  entries?: BrowseEntry[];
  truncated?: boolean;
  error?: string;
}

interface DataPanelProps {
  nodeId: string;
  workflowId?: string;
  parameters: Record<string, unknown>;
  onParameterChange: (name: string, value: unknown) => void;
}

const formatSize = (bytes?: number): string => {
  if (!bytes && bytes !== 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const DataPanel: React.FC<DataPanelProps> = ({
  nodeId,
  workflowId,
  parameters,
  onParameterChange,
}) => {
  const { sendRequest } = useWebSocket();
  const queryClient = useQueryClient();
  const [browsePath, setBrowsePath] = useState('');
  const [draftName, setDraftName] = useState('');
  const [draftPath, setDraftPath] = useState('');
  const [draftWritable, setDraftWritable] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);

  const enabledMounts = Array.isArray(parameters.mounts)
    ? (parameters.mounts as string[])
    : [];

  const mountsQuery = useQuery<MountsResponse, Error>({
    queryKey: ['dataMounts'],
    queryFn: () => sendRequest<MountsResponse>('data_list_mounts', {}),
  });
  const mounts = mountsQuery.data?.mounts ?? [];

  const browseQuery = useQuery<BrowseResponse, Error>({
    queryKey: ['dataBrowse', workflowId ?? '', nodeId, browsePath],
    queryFn: () =>
      sendRequest<BrowseResponse>('data_browse', {
        workflow_id: workflowId,
        data_node_id: nodeId,
        path: browsePath,
        limit: BROWSE_LIMIT,
      }),
    enabled: !!workflowId && !!nodeId,
  });
  const browse = browseQuery.data;

  const invalidateMounts = async () => {
    await queryClient.invalidateQueries({ queryKey: ['dataMounts'] });
    await queryClient.invalidateQueries({
      queryKey: ['dataBrowse', workflowId ?? '', nodeId],
    });
  };

  const addMount = useMutation({
    mutationFn: () =>
      sendRequest<MountsResponse>('data_add_mount', {
        name: draftName.trim(),
        root_path: draftPath.trim(),
        writable: draftWritable,
      }),
    onSuccess: async (result) => {
      if (result.success === false) {
        toast.error(result.error || 'Mount rejected');
        return;
      }
      setDraftName('');
      setDraftPath('');
      setDraftWritable(false);
      toast.success('Mount added');
      await invalidateMounts();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const updateMount = useMutation({
    mutationFn: (input: { name: string; writable: boolean }) =>
      sendRequest<MountsResponse>('data_update_mount', input),
    onSuccess: async (result) => {
      if (result.success === false) {
        toast.error(result.error || 'Update rejected');
        return;
      }
      await invalidateMounts();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const removeMount = useMutation({
    mutationFn: (name: string) =>
      sendRequest<MountsResponse>('data_remove_mount', { name }),
    onSuccess: async (result, name) => {
      if (result.success === false) {
        toast.error(result.error || 'Remove rejected');
        return;
      }
      // Drop the removed name from this node's enabled subset too.
      if (enabledMounts.includes(name)) {
        onParameterChange(
          'mounts',
          enabledMounts.filter((mount) => mount !== name),
        );
      }
      toast.success('Mount removed');
      await invalidateMounts();
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const toggleEnabled = (name: string, enabled: boolean) => {
    const next = enabled
      ? [...enabledMounts.filter((mount) => mount !== name), name]
      : enabledMounts.filter((mount) => mount !== name);
    onParameterChange('mounts', next);
  };

  const navigateTo = (entry: BrowseEntry) => {
    if (!entry.is_dir) return;
    setBrowsePath(entry.path ?? entry.location ?? '');
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-y-auto p-1">
      {/* Sources: machine-wide mounts + this node's enabled subset */}
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-foreground">Sources</h3>
          <span className="text-xs text-muted-foreground">
            Machine-wide — shared by every Data node
          </span>
        </div>

        <div className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2">
          <Home className="h-4 w-4 text-muted-foreground" />
          <span className="flex-1 text-sm">Workspace</span>
          <Badge variant="outline">read + write</Badge>
          <Badge variant="secondary">always on</Badge>
        </div>

        {mountsQuery.isLoading ? (
          <div className="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading mounts…
          </div>
        ) : (
          mounts.map((mount) => (
            <div
              key={mount.name}
              className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2"
            >
              <Checkbox
                checked={enabledMounts.includes(mount.name)}
                onCheckedChange={(checked) =>
                  toggleEnabled(mount.name, checked === true)
                }
                aria-label={`Expose ${mount.name} to this node`}
              />
              <HardDrive className="h-4 w-4 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{mount.name}</div>
                <div
                  className="truncate text-xs text-muted-foreground"
                  title={mount.root_path}
                >
                  {mount.root_path}
                </div>
              </div>
              <div className="flex items-center gap-1">
                <Switch
                  checked={mount.writable}
                  onCheckedChange={(checked) =>
                    updateMount.mutate({
                      name: mount.name,
                      writable: checked === true,
                    })
                  }
                  aria-label={`Writable flag for ${mount.name}`}
                />
                <span className="text-xs text-muted-foreground">
                  {mount.writable ? 'read + write' : 'read-only'}
                </span>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setRemoveTarget(mount.name)}
                aria-label={`Remove mount ${mount.name}`}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))
        )}

        <div className="flex flex-col gap-2 rounded-md border border-dashed border-border p-3">
          <div className="flex gap-2">
            <Input
              value={draftName}
              onChange={(event) => setDraftName(event.target.value)}
              placeholder="mount name (e.g. reports)"
              className="w-40"
            />
            <Input
              value={draftPath}
              onChange={(event) => setDraftPath(event.target.value)}
              placeholder="absolute folder path"
              className="flex-1"
            />
          </div>
          <div className="flex items-center justify-between">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Switch
                checked={draftWritable}
                onCheckedChange={(checked) =>
                  setDraftWritable(checked === true)
                }
                aria-label="New mount writable"
              />
              writable
            </label>
            <Button
              variant="invert"
              size="sm"
              onClick={() => addMount.mutate()}
              disabled={
                addMount.isPending || !draftName.trim() || !draftPath.trim()
              }
            >
              {addMount.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              ) : (
                <Plus className="mr-1 h-4 w-4" />
              )}
              Add mount
            </Button>
          </div>
        </div>
      </section>

      {/* Read-only browser over workspace + enabled mounts */}
      <section className="flex min-h-0 flex-1 flex-col gap-2">
        <h3 className="text-sm font-medium text-foreground">Browse</h3>
        <div className="flex flex-wrap items-center gap-1 text-xs">
          <Button
            variant={browsePath === '' ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setBrowsePath('')}
          >
            <Home className="mr-1 h-3 w-3" /> Workspace
          </Button>
          {mounts
            .filter((mount) => enabledMounts.includes(mount.name))
            .map((mount) => (
              <Button
                key={mount.name}
                variant={
                  browsePath.startsWith(`mnt/${mount.name}`)
                    ? 'secondary'
                    : 'ghost'
                }
                size="sm"
                onClick={() => setBrowsePath(`mnt/${mount.name}`)}
              >
                <HardDrive className="mr-1 h-3 w-3" /> {mount.name}
              </Button>
            ))}
        </div>

        {(browse?.crumbs?.length ?? 0) > 0 && (
          <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
            {browse?.crumbs?.map((crumb) => (
              <React.Fragment key={crumb.path}>
                <span>/</span>
                <button
                  type="button"
                  className="hover:text-foreground"
                  onClick={() => setBrowsePath(crumb.path)}
                >
                  {crumb.name}
                </button>
              </React.Fragment>
            ))}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-border">
          {browseQuery.isLoading ? (
            <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading…
            </div>
          ) : browse?.success === false ? (
            <div className="p-3 text-sm text-destructive">{browse.error}</div>
          ) : (browse?.entries?.length ?? 0) === 0 ? (
            <div className="p-3 text-sm text-muted-foreground">
              Empty folder
            </div>
          ) : (
            <ul>
              {browse?.entries?.map((entry) => (
                <li key={entry.path ?? entry.location ?? entry.name}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-bg-hover"
                    onClick={() => navigateTo(entry)}
                    disabled={!entry.is_dir}
                  >
                    {entry.is_dir ? (
                      <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <File className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )}
                    <span className="min-w-0 flex-1 truncate">
                      {entry.name}
                    </span>
                    {!entry.is_dir && (
                      <span className="text-xs text-muted-foreground">
                        {formatSize(entry.size_bytes)}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {browse?.truncated && (
          <div className="text-xs text-muted-foreground">
            Listing truncated at {BROWSE_LIMIT} entries
          </div>
        )}
      </section>

      <AlertDialog
        open={removeTarget !== null}
        onOpenChange={(open) => !open && setRemoveTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove mount?</AlertDialogTitle>
            <AlertDialogDescription>
              Every Data node on this machine loses access to
              {' '}
              <span className="font-mono">{removeTarget}</span>. Files on disk
              are not touched.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="invert"
              onClick={() => {
                if (removeTarget) removeMount.mutate(removeTarget);
                setRemoveTarget(null);
              }}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default DataPanel;
