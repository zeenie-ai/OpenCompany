/**
 * McpConnectorPanel — a custom MCP connector (kind `mcp`): the server it
 * reaches and how it signs in; Test, which asks the server again; Refresh,
 * which reads its tools again and holds any change for Accept or Discard,
 * since a server can change what a tool says it does; Sign in again, for
 * one that signs in with OAuth (its sign-in page opens in a new tab); Remove;
 * and its tools, each on or off for employees and set to ask first or not.
 *
 * Everything shown is the connector's card (`config.mcp`). Every change is
 * the server's (nodes/mcp/_handlers.py): a command waits for the refetched
 * card, so the panel never shows a state the server does not hold.
 */

import { useId, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ActionButton } from '@/components/ui/action-button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
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
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { CREDENTIAL_PROBE_REQUEST_TIMEOUT, useWebSocketActions } from '@/contexts/WebSocketContext';
import { CATALOGUE_QUERY_KEY, type ServerMcpConnector, type ServerMcpTool } from '@/hooks/useCatalogueQuery';
import { cn } from '@/lib/utils';
import { formatTimestamp } from '@/utils/formatters';
import type { CredentialPanelProps } from '../PanelRenderer';

interface Result {
  success?: boolean;
  error?: string;
}

type ToolChange = { enabled?: boolean; ask?: boolean };

const FAILED = "That didn't work. Try again.";

function signInText(signIn: ServerMcpConnector['sign_in']): string {
  if (signIn.kind === 'bearer') return 'Bearer token';
  if (signIn.kind === 'header') return `Header ${signIn.header ?? ''}`.trim();
  if (signIn.kind === 'oauth') return 'OAuth';
  return 'None';
}

function pendingLines(pending: NonNullable<ServerMcpConnector['pending']>): string[] {
  return [
    pending.added.length > 0 ? `New: ${pending.added.join(', ')}` : null,
    pending.removed.length > 0 ? `Gone: ${pending.removed.join(', ')}` : null,
    pending.changed.length > 0 ? `Changed: ${pending.changed.join(', ')}` : null,
    pending.instructions ? "The server's instructions changed." : null,
  ].filter((line): line is string => line !== null);
}

export default function McpConnectorPanel({ config, showTechnicalSections = false, onLeave }: CredentialPanelProps) {
  const { sendRequest, isReady } = useWebSocketActions();
  const queryClient = useQueryClient();
  const headingId = useId();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  // The switch being changed shows its new position until the card does.
  const [changing, setChanging] = useState<({ tool: string } & ToolChange) | null>(null);
  const [removing, setRemoving] = useState(false);
  const mcp = config.mcp;
  if (!mcp) return null;

  /** Sends one command for this connector, then waits for the refetched card. */
  const run = async <T extends Result>(key: string, command: string, data: Record<string, unknown> = {}): Promise<T | null> => {
    setBusy(key);
    setError('');
    setNotice(null);
    try {
      const result = await sendRequest<T>(command, { ref: config.id, ...data }, CREDENTIAL_PROBE_REQUEST_TIMEOUT);
      if (result.success === false) throw new Error(result.error || FAILED);
      await queryClient.invalidateQueries({ queryKey: CATALOGUE_QUERY_KEY });
      return result;
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : FAILED);
      return null;
    } finally {
      setBusy(null);
    }
  };

  const test = async () => {
    const result = await run<Result & { ok?: boolean; message?: string }>('test', 'mcp_connector_test');
    if (result) setNotice({ ok: result.ok === true, text: result.message ?? '' });
  };

  const refresh = async () => {
    const result = await run<Result & { changes?: unknown }>('refresh', 'mcp_connector_refresh');
    if (result && !result.changes) setNotice({ ok: true, text: 'Nothing changed: the tools are as you accepted them.' });
  };

  const signIn = async () => {
    const result = await run<Result & { sign_in_url?: string }>('sign_in', 'mcp_connector_sign_in');
    if (!result?.sign_in_url) return;
    window.open(result.sign_in_url, '_blank');
    setNotice({ ok: true, text: 'Sign in on the page that opened.' });
  };

  const setTool = async (tool: string, change: ToolChange) => {
    setChanging({ tool, ...change });
    await run('tool', 'mcp_connector_set_tool', { tool, ...change });
    setChanging(null);
  };

  const remove = async () => {
    setRemoving(false);
    if (await run('remove', 'mcp_connector_remove')) {
      toast.info(`${config.name} removed`);
      onLeave?.();
    }
  };

  const shown = (tool: ServerMcpTool): ServerMcpTool =>
    changing?.tool === tool.name ? { ...tool, enabled: changing.enabled ?? tool.enabled, ask: changing.ask ?? tool.ask } : tool;
  const server = [mcp.server.title || mcp.server.name, mcp.server.version].filter(Boolean).join(' ');
  const locked = !isReady || busy !== null;

  return (
    <div className="flex flex-col gap-4 p-5">
      {config.instructions && <p className="m-0 text-sm text-muted-foreground">{config.instructions}</p>}

      <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
        <dt className="text-muted-foreground">Server</dt>
        <dd className="m-0 truncate">{server || 'No name given'}</dd>
        <dt className="text-muted-foreground">Address</dt>
        <dd className="m-0 truncate font-mono text-xs leading-5">{mcp.address}</dd>
        <dt className="text-muted-foreground">Sign-in</dt>
        <dd className="m-0">{signInText(mcp.sign_in)}</dd>
        {showTechnicalSections && (
          <>
            <dt className="text-muted-foreground">Transport</dt>
            <dd className="m-0">{mcp.transport === 'sse' ? 'SSE' : 'Streamable HTTP'}</dd>
          </>
        )}
        <dt className="text-muted-foreground">Tools read</dt>
        <dd className="m-0">{formatTimestamp(mcp.read_at)}</dd>
      </dl>

      <div className="flex flex-wrap items-center gap-2">
        <ActionButton intent="run" disabled={locked} onClick={() => void test()}>
          {busy === 'test' ? 'Testing…' : 'Test'}
        </ActionButton>
        <ActionButton intent="config" disabled={locked} onClick={() => void refresh()}>
          {busy === 'refresh' ? 'Reading tools…' : 'Refresh tools'}
        </ActionButton>
        {mcp.sign_in.kind === 'oauth' && (
          <ActionButton intent="secret" disabled={locked} onClick={() => void signIn()}>
            {busy === 'sign_in' ? 'Opening sign-in…' : 'Sign in again'}
          </ActionButton>
        )}
        <ActionButton intent="stop" disabled={locked} onClick={() => setRemoving(true)} className="ml-auto">
          Remove
        </ActionButton>
      </div>
      {notice && (
        <p role="status" className={cn('m-0 text-sm', notice.ok ? 'text-success' : 'text-destructive')}>
          {notice.text}
        </p>
      )}
      {error && (
        <p role="alert" className="m-0 text-sm text-destructive">
          {error}
        </p>
      )}

      {mcp.pending && (
        <Alert variant="warning">
          <AlertTitle>The server changed its tools</AlertTitle>
          <AlertDescription className="flex flex-col gap-2">
            <ul className="m-0 list-none p-0">
              {pendingLines(mcp.pending).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <p className="m-0">Employees keep the tools you accepted until you accept this change.</p>
            <div className="flex gap-2">
              <ActionButton intent="run" disabled={locked} onClick={() => void run('accept', 'mcp_connector_review', { accept: true })}>
                Accept
              </ActionButton>
              <Button variant="ghost" size="sm" disabled={locked} onClick={() => void run('discard', 'mcp_connector_review', { accept: false })}>
                Discard
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      <section aria-labelledby={headingId} className="flex flex-col gap-2">
        <div className="flex items-baseline gap-2">
          <h3 id={headingId} className="m-0 text-sm font-medium">
            Tools
          </h3>
          <span className="font-mono text-xs text-muted-foreground">{mcp.tools.length}</span>
        </div>
        <p className="m-0 text-xs text-muted-foreground">
          While an employee asks first, a tool set to Ask first waits for your OK before it runs.
        </p>
        {mcp.tools.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">The server offers no tools.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {mcp.tools.map((tool) => (
              <ToolRow key={tool.name} tool={shown(tool)} disabled={locked} onChange={(change) => void setTool(tool.name, change)} />
            ))}
          </ul>
        )}
      </section>

      <AlertDialog open={removing} onOpenChange={setRemoving}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {config.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Employees stop using its tools. You can add it again from its address.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep it</AlertDialogCancel>
            <AlertDialogAction onClick={() => void remove()}>Remove</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ToolRow({ tool, disabled, onChange }: { tool: ServerMcpTool; disabled: boolean; onChange: (change: ToolChange) => void }) {
  const id = useId();
  const label = tool.title || tool.name;
  return (
    <li className="flex items-start gap-3 rounded-md border border-border bg-card p-3">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium">{label}</span>
          {tool.read_only && <Badge variant="secondary">Read-only</Badge>}
        </div>
        {tool.title && <span className="truncate font-mono text-xs text-muted-foreground">{tool.name}</span>}
        {tool.description && <p className="m-0 line-clamp-2 text-xs text-muted-foreground">{tool.description}</p>}
        {!tool.usable && <p className="m-0 text-xs text-warning">Can't be used. {tool.reason}</p>}
      </div>
      {tool.usable && (
        <div className="grid shrink-0 grid-cols-[auto_auto] items-center gap-x-2 gap-y-1.5 text-xs">
          <Label htmlFor={`${id}-use`} className="text-xs font-normal">
            Use
          </Label>
          <Switch
            id={`${id}-use`}
            size="sm"
            checked={tool.enabled}
            disabled={disabled}
            aria-label={`Let employees use ${label}`}
            onCheckedChange={(enabled) => onChange({ enabled })}
          />
          <Label htmlFor={`${id}-ask`} className="text-xs font-normal">
            Ask first
          </Label>
          <Switch
            id={`${id}-ask`}
            size="sm"
            checked={tool.ask}
            disabled={disabled || !tool.enabled}
            aria-label={`Ask first before ${label}`}
            onCheckedChange={(ask) => onChange({ ask })}
          />
        </div>
      )}
    </li>
  );
}
