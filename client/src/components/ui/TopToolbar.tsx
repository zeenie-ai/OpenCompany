import React, { useState, useEffect, useCallback } from 'react';
import {
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  FileText,
  ChevronDown,
  FilePlus,
  FolderOpen,
  Upload,
  Download,
  Clipboard,
  Pencil,
  Settings as SettingsIcon,
  KeyRound,
  LogOut,
  Save,
  Play,
  Pause,
  RotateCcw,
  LoaderCircle,
  Repeat,
  Clock,
  Zap,
  Lock,
  Monitor,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { ActionButton } from '@/components/ui/action-button';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  isWorkflowControlTransitioning,
  type WorkflowControlPendingMutation,
  type WorkflowControlStatus,
} from '../../contexts/WebSocketContext';
import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import { cn } from '@/lib/utils';
import { featureFlags } from '@/lib/featureFlags';
import { ModeToggle } from '@/components/shell/ModeToggle';
import { useCanvasDockStore } from '../../stores/canvasDockStore';
import { useAuth } from '../../contexts/AuthContext';
import { useApiKeys, GlobalModelState } from '../../hooks/useApiKeys';
import { useStoredCredentialSignature } from '../../hooks/useCatalogueQuery';
import { AI_PROVIDER_META } from '../icons/AIProviderIcons';

// New-contract token: --border-default ↔ Tailwind utility `bg-border-default`.
// Same colour as `bg-border` under light/dark, retints automatically under
// renaissance / cyber via the per-theme [data-theme="..."] block.
const Divider = () => <div className="mx-1 h-6 w-px bg-border-default" />;

interface TopToolbarProps {
  workflowName: string;
  onWorkflowNameChange: (name: string) => void;
  onSave: () => void;
  onNew: () => void;
  onOpen: () => void;
  onRun: () => void;
  workflowControl: WorkflowControlStatus;
  workflowControlPending?: WorkflowControlPendingMutation;
  onStartWorkflow: () => void;
  onPauseWorkflow: () => void;
  onResumeWorkflow: () => void;
  onResetWorkflow: () => void;
  hasUnsavedChanges: boolean;
  sidebarVisible: boolean;
  onToggleSidebar: () => void;
  componentPaletteVisible: boolean;
  onToggleComponentPalette: () => void;
  proMode: boolean;
  onToggleProMode: () => void;
  onOpenSettings: () => void;
  onOpenCredentials: () => void;
  onExportJSON: () => void;
  onExportFile: () => void;
  onImportJSON: () => void;
  onGlobalModelChange?: (provider: string, model: string) => void;
  onOverrideAllAgents?: (provider: string, model: string) => void;
}

const TopToolbar: React.FC<TopToolbarProps> = ({
  workflowName,
  onWorkflowNameChange,
  onSave,
  onNew,
  onOpen,
  onRun: _onRun,
  workflowControl,
  workflowControlPending,
  onStartWorkflow,
  onPauseWorkflow,
  onResumeWorkflow,
  onResetWorkflow,
  hasUnsavedChanges,
  sidebarVisible,
  onToggleSidebar,
  componentPaletteVisible,
  onToggleComponentPalette,
  proMode,
  onToggleProMode,
  onOpenSettings,
  onOpenCredentials,
  onExportJSON,
  onExportFile,
  onImportJSON,
  onGlobalModelChange,
  onOverrideAllAgents,
}) => {
  const [isEditing, setIsEditing] = useState(false);
  // Dock state lives on its own store (not a prop): slice reads scope
  // re-renders to this button, and the state isn't Dashboard's to thread.
  const canvasDockOpen = useCanvasDockStore((s) => s.open);
  const toggleCanvasDock = useCanvasDockStore((s) => s.toggle);
  const [resetOpen, setResetOpen] = useState(false);
  const [tempName, setTempName] = useState(workflowName);
  const { user, logout } = useAuth();
  const hasPendingControlMutation = Boolean(workflowControlPending);
  const isAuthoritativeTransition = isWorkflowControlTransitioning(workflowControl);
  const canPauseWorkflow = workflowControl.can_pause || workflowControl.state === 'pausing';
  const canResumeWorkflow = workflowControl.can_resume || workflowControl.state === 'resuming';
  const isRetryingReset = workflowControl.state === 'resetting';
  const controlTransitionLabel = (
    workflowControlPending?.state ?? workflowControl.state
  ).replace('_', ' ');

  // Global Model Selector state
  const { getValidatedAiProviders, saveGlobalModel, isConnected: apiKeysConnected } = useApiKeys();
  const [globalModelState, setGlobalModelState] = useState<GlobalModelState>({ providers: [], global_provider: null, global_model: null });

  // Re-fetch validated providers whenever the stored credentials change:
  // a provider added or removed, or a named endpoint added, removed or
  // refreshed. Read from the catalogue (single source of truth); a count of
  // stored providers would miss a second endpoint.
  const storedSignature = useStoredCredentialSignature();
  useEffect(() => {
    if (!apiKeysConnected) return;
    getValidatedAiProviders().then(state => setGlobalModelState(state));
  }, [apiKeysConnected, storedSignature, getValidatedAiProviders]);

  const handleSelectGlobalModel = useCallback((value: string) => {
    const [provider, ...rest] = value.split('::');
    const model = rest.join('::');
    setGlobalModelState(prev => ({ ...prev, global_provider: provider, global_model: model }));
    saveGlobalModel(provider, model);
    onGlobalModelChange?.(provider, model);
  }, [saveGlobalModel, onGlobalModelChange]);

  const globalSelectValue = globalModelState.global_provider && globalModelState.global_model
    ? `${globalModelState.global_provider}::${globalModelState.global_model}` : undefined;
  const selectedProviderMeta = AI_PROVIDER_META[globalModelState.global_provider || ''];

  const handleNameClick = () => {
    setTempName(workflowName);
    setIsEditing(true);
  };

  const handleNameSubmit = () => {
    onWorkflowNameChange(tempName.trim() || 'Untitled Workflow');
    setIsEditing(false);
  };

  const handleNameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleNameSubmit();
    else if (e.key === 'Escape') {
      setTempName(workflowName);
      setIsEditing(false);
    }
  };

  return (
    // border-default + bg-bg-panel are the new-contract tokens. They
    // resolve to the existing colours under light/dark (no visual
    // change) but pick up the parchment / void surfaces under
    // renaissance / cyber automatically.
    // `toolbar` is the design-handoff structural class — per-theme CSS
    // attaches panel textures (vellum on Renaissance, scanlines on Cyber,
    // marble veins on Greek, riveted leather on Steampunk, etc.) +
    // border treatments via `:root[data-theme="..."] .toolbar`.
    <div className="toolbar flex h-12 items-center justify-between gap-3 border-b border-border-default bg-bg-panel px-3">
      {/* ---------- Left Section ---------- */}
      {/* Design-handoff layout contract: every toolbar cluster is pinned
          (shrink-0) except the workflow-name chip, which is the ONE
          shrinkable group. Wide-tracking themes (Cyber/Greek uppercase at
          0.10-0.18em) widen every label; without pinning, the mode toggle
          and action cluster collapse before the name does. */}
      <div className="flex shrink-0 items-center gap-1.5">
        <Button
          variant="outline"
          size="icon-sm"
          onClick={onToggleSidebar}
          aria-pressed={sidebarVisible}
          title={sidebarVisible ? 'Hide sidebar' : 'Show sidebar'}
          className="border-action-save-border bg-action-save-soft text-action-save-ink hover:bg-action-save-hover aria-pressed:bg-action-save-hover"
        >
          {sidebarVisible ? <PanelLeftClose /> : <PanelLeftOpen />}
        </Button>

        <Divider />

        {/* File menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="border-action-save-border bg-action-save-soft text-action-save-ink hover:bg-action-save-hover"
            >
              <FileText />
              {/* font-display + tracking-display drive the per-theme display
                  font + letter-spacing; uppercase is gated by --type-uppercase
                  via the new-contract `tracking-display` token (light/dark
                  use 0 + none, renaissance + cyber turn it on). */}
              <span className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)]">
                File
              </span>
              <ChevronDown />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="min-w-[200px]">
            <DropdownMenuLabel className="text-xs uppercase tracking-wider text-muted-foreground">
              File Operations
            </DropdownMenuLabel>
            <DropdownMenuItem onSelect={onNew} className="text-action-run focus:text-action-run">
              <FilePlus />
              New Workflow
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onOpen} className="text-action-save focus:text-action-save">
              <FolderOpen />
              Open
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onExportFile} className="text-action-save focus:text-action-save">
              <Download />
              Export
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onImportJSON} className="text-action-save focus:text-action-save">
              <Upload />
              Import
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onExportJSON} className="text-action-tools focus:text-action-tools">
              <Clipboard />
              Copy as JSON
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* ---------- Center: Workflow Name (the one shrinkable group) ---------- */}
      <div className="flex min-w-0 flex-1 justify-center">
        {isEditing ? (
          <input
            type="text"
            value={tempName}
            onChange={(e) => setTempName(e.target.value)}
            onBlur={handleNameSubmit}
            onKeyDown={handleNameKeyDown}
            autoFocus
            className="min-w-[200px] max-w-full rounded-sm border border-accent bg-background px-3 py-1.5 text-center text-sm font-medium text-foreground outline-none"
          />
        ) : (
          <button
            onClick={handleNameClick}
            title={`${workflowName} — click to rename`}
            className="flex min-w-0 max-w-full items-center gap-1.5 rounded-sm bg-transparent px-3 py-1.5 transition-colors hover:bg-bg-hover"
          >
            {/* font-display + tracking-display + [text-transform] are
                theme-driven via the new-contract typography tokens. Under
                light/dark the workflow name reads as our regular sans-serif;
                under Renaissance it becomes Cinzel uppercase, under Cyber it
                becomes Space Mono uppercase. */}
            <span className="truncate text-sm font-display font-medium tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
              {workflowName}
            </span>
            <Pencil className="h-3 w-3 shrink-0 text-fg-muted" />
          </button>
        )}
      </div>

      {/* ---------- Global Model Selector (pinned) ---------- */}
      {globalModelState.providers.length > 0 && (
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-sm font-semibold whitespace-nowrap text-node-model">
            Set Global Model
          </span>
          <Select value={globalSelectValue} onValueChange={handleSelectGlobalModel}>
            <SelectTrigger className="h-8 w-auto min-w-[180px]">
              <SelectValue placeholder="Select model...">
                {globalModelState.global_model && (
                  <span className="flex items-center gap-2">
                    {selectedProviderMeta && (
                      <span
                        className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: selectedProviderMeta.color }}
                      />
                    )}
                    <span>{globalModelState.global_model}</span>
                  </span>
                )}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {globalModelState.providers.map((vp) => {
                const meta = AI_PROVIDER_META[vp.provider];
                const models = vp.popular_models.length > 0 ? vp.popular_models : vp.models.slice(0, 5);
                return (
                  <SelectGroup key={vp.provider}>
                    <SelectLabel
                      className="text-xs"
                      style={{ color: meta?.color }}
                    >
                      {vp.display_name || meta?.label || vp.provider}
                    </SelectLabel>
                    {models.map((m) => (
                      <SelectItem key={`${vp.provider}::${m}`} value={`${vp.provider}::${m}`}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                );
              })}
            </SelectContent>
          </Select>
          {globalSelectValue && (
            <ActionButton
              intent="config"
              onClick={() => globalModelState.global_provider && globalModelState.global_model && onOverrideAllAgents?.(globalModelState.global_provider, globalModelState.global_model)}
              title="Override all agent nodes in this workflow to use the selected model"
              className="h-8 px-3 text-xs"
            >
              <Repeat className="h-3 w-3" />
              Apply All
            </ActionButton>
          )}
        </div>
      )}

      {/* ---------- Right Section (pinned: mode toggle + actions + save state) ---------- */}
      <div className="flex shrink-0 items-center gap-1.5">
        {/* Mode Toggle - segmented control. With Normal mode on it switches
            screens (Home / this editor); otherwise it filters the palette. */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-node-model">Mode:</span>
          {featureFlags.normalMode ? <ModeToggle /> : <div
            className="flex items-center rounded-md border border-border bg-card p-0.5"
            title={proMode ? 'Dev mode: All components visible' : 'Normal mode: Only AI components'}
          >
            <button
              onClick={() => proMode && onToggleProMode()}
              className={cn(
                'flex items-center gap-1 rounded-sm border px-2.5 py-1 text-xs font-semibold transition-all',
                !proMode
                  ? 'border-node-skill-border bg-node-skill-soft text-node-skill cursor-default'
                  : 'cursor-pointer border-transparent text-node-workflow hover:bg-muted'
              )}
            >
              <Clock className="h-3 w-3" />
              Normal
            </button>
            <button
              onClick={() => !proMode && onToggleProMode()}
              className={cn(
                'flex items-center gap-1 rounded-sm border px-2.5 py-1 text-xs font-semibold transition-all',
                proMode
                  ? 'border-node-agent-border bg-node-agent-soft text-node-agent cursor-default'
                  : 'cursor-pointer border-transparent text-node-workflow hover:bg-muted'
              )}
            >
              <Zap className="h-3 w-3" />
              Dev
            </button>
          </div>}
        </div>

        <Divider />

        <Button
          variant="outline"
          size="icon-sm"
          onClick={onOpenSettings}
          title="Settings"
          className="border-action-config-border bg-action-config-soft text-action-config-ink hover:bg-action-config-hover"
        >
          <SettingsIcon />
        </Button>

        <Button
          variant="outline"
          size="icon-sm"
          onClick={onOpenCredentials}
          title="API Credentials"
          className="border-action-secret-border bg-action-secret-soft text-action-secret-ink hover:bg-action-secret-hover"
        >
          <KeyRound />
        </Button>

        <ThemeSwitcher />

        {user && (
          <Button
            variant="outline"
            size="icon-sm"
            onClick={logout}
            title={`Logout ${user.display_name}`}
            className="border-action-stop-border bg-action-stop-soft text-action-stop-ink hover:bg-action-stop-hover"
          >
            <LogOut />
          </Button>
        )}

        <Divider />

        {/* Durable workflow lifecycle */}
        {hasPendingControlMutation ? (
          <ActionButton
            intent="run"
            disabled
            title={`Workflow is ${controlTransitionLabel}`}
          >
            <LoaderCircle className="h-3 w-3 animate-spin" />
            {controlTransitionLabel}
          </ActionButton>
        ) : workflowControl.can_start ? (
          <ActionButton
            intent="run"
            onClick={onStartWorkflow}
            title="Start workflow"
          >
            <Play className="h-3 w-3 fill-current" />
            Start
          </ActionButton>
        ) : canResumeWorkflow ? (
          <ActionButton
            intent="run"
            onClick={onResumeWorkflow}
            title={workflowControl.state === 'resuming'
              ? 'Retry the interrupted resume transition'
              : 'Resume this workflow execution'}
          >
            <Play className="h-3 w-3 fill-current" />
            {workflowControl.state === 'resuming' ? 'Retry Resume' : 'Resume'}
          </ActionButton>
        ) : canPauseWorkflow ? (
          <ActionButton
            intent="stop"
            onClick={onPauseWorkflow}
            title={workflowControl.state === 'pausing'
              ? 'Retry the interrupted pause transition'
              : 'Pause new workflow scheduling after in-flight work finishes'}
          >
            <Pause className="h-3 w-3 fill-current" />
            {workflowControl.state === 'pausing' ? 'Retry Pause' : 'Pause'}
          </ActionButton>
        ) : isAuthoritativeTransition ? (
          <ActionButton
            intent="run"
            disabled
            title={`Workflow is ${controlTransitionLabel}`}
          >
            <LoaderCircle className="h-3 w-3 animate-spin" />
            {controlTransitionLabel}
          </ActionButton>
        ) : (
          <ActionButton intent="run" disabled title={`Workflow is ${workflowControl.state}`}>
            {workflowControl.state.replace('_', ' ')}
          </ActionButton>
        )}

        {workflowControl.can_reset && (
          <ActionButton
            intent="stop"
            onClick={() => setResetOpen(true)}
            disabled={hasPendingControlMutation}
            title={isRetryingReset
              ? 'Retry the interrupted reset transition'
              : 'Terminate and archive this execution'}
          >
            <RotateCcw className="h-3 w-3" />
            {isRetryingReset ? 'Retry Reset' : 'Reset'}
          </ActionButton>
        )}

        {/* Canvas-lock indicator — renders the server-owned `can_edit`
            capability so a silently-dead drag never reads as broken UI. */}
        {workflowControl.can_edit === false && (
          <Badge
            variant="outline"
            className="gap-1 text-muted-foreground"
            title="Canvas locked while the workflow runs — pause it to edit"
          >
            <Lock className="h-3 w-3" />
            Locked
          </Badge>
        )}

        <AlertDialog open={resetOpen} onOpenChange={setResetOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Reset workflow execution?</AlertDialogTitle><AlertDialogDescription>This immediately terminates active work and archives the current generation. Task history remains available. The workflow will remain stopped until you press Start.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>Keep current execution</AlertDialogCancel><AlertDialogAction disabled={hasPendingControlMutation} onClick={onResetWorkflow}>{isRetryingReset ? 'Retry reset' : 'Reset workflow'}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>

        <ActionButton
          intent="save"
          onClick={() => typeof onSave === 'function' && onSave()}
          disabled={!hasUnsavedChanges}
          title={hasUnsavedChanges ? 'Save changes' : 'No changes to save'}
        >
          <Save className="h-3 w-3" />
          Save
        </ActionButton>

        {/* Status Indicator — font-mono tracks the new-contract --font-mono
            so renaissance gets IM Fell English and cyber gets JetBrains
            Mono. Stays system mono under light/dark. */}
        <div
          className={cn(
            'flex items-center gap-2 rounded-sm px-3 py-1 text-xs font-mono',
            hasUnsavedChanges ? 'text-warning' : 'text-success'
          )}
        >
          <div
            className={cn(
              'h-2 w-2 rounded-full',
              hasUnsavedChanges ? 'bg-warning' : 'bg-success'
            )}
          />
          {hasUnsavedChanges ? 'Modified' : 'Saved'}
        </div>

        <Divider />

        <Button
          variant="outline"
          size="icon-sm"
          onClick={onToggleComponentPalette}
          aria-pressed={componentPaletteVisible}
          title={componentPaletteVisible ? 'Hide components' : 'Show components'}
          className="border-action-tools-border bg-action-tools-soft text-action-tools-ink hover:bg-action-tools-hover aria-pressed:bg-action-tools-hover"
        >
          {componentPaletteVisible ? <PanelRightClose /> : <PanelRightOpen />}
        </Button>

        <Button
          variant="outline"
          size="icon-sm"
          onClick={toggleCanvasDock}
          aria-pressed={canvasDockOpen}
          title={canvasDockOpen ? 'Hide canvas' : 'Show canvas'}
          className="border-action-tools-border bg-action-tools-soft text-action-tools-ink hover:bg-action-tools-hover aria-pressed:bg-action-tools-hover"
        >
          <Monitor />
        </Button>
      </div>
    </div>
  );
};

export default TopToolbar;
