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
  isWorkflowResetBlocked,
  type WorkflowControlPendingMutation,
  type WorkflowControlStatus,
} from '../../contexts/WebSocketContext';
import { ThemeButton } from '@/components/ui/ThemeButton';
import { cn } from '@/lib/utils';
import { featureFlags } from '@/lib/featureFlags';
import { ModeToggle } from '@/components/shell/ModeToggle';
import { useCanvasDockStore } from '../../stores/canvasDockStore';
import { useAuth } from '../../contexts/AuthContext';
import { useApiKeys, GlobalModelState } from '../../hooks/useApiKeys';
import { useStoredCredentialSignature } from '../../hooks/useCatalogueQuery';
import { AI_PROVIDER_META } from '../icons/AIProviderIcons';

// New-contract token: --border-default ↔ Tailwind utility `bg-border-default`.
// Same colour as `bg-border` under light/dark.
const Divider = () => <div className="mx-1 h-6 w-px bg-border-default" />;

// Colour marks state or role (docs-internal/theme_system.md): the toolbar's
// controls are neutral outlines. Green only starts the workflow (Start,
// Resume), and the save dot shows a state.
const NEUTRAL = 'border-border-default text-fg-default';
// A panel's toggle is filled while its panel shows.
const NEUTRAL_TOGGLE = `${NEUTRAL} aria-pressed:bg-bg-active`;
// A labelled button beside Start, in ActionButton's shape.
const NEUTRAL_ACTION = `${NEUTRAL} rounded-md px-3.5 text-[13px] font-semibold`;

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
  const resetBlocked = isWorkflowResetBlocked(workflowControlPending);
  const isAuthoritativeTransition = isWorkflowControlTransitioning(workflowControl);
  const canPauseWorkflow = workflowControl.can_pause || workflowControl.state === 'pausing';
  const canResumeWorkflow = workflowControl.can_resume || workflowControl.state === 'resuming';
  const isRetryingReset = workflowControl.state === 'resetting';
  const transitionState = workflowControlPending?.state ?? workflowControl.state;
  const controlTransitionLabel = transitionState === 'pausing' ? 'Stopping…'
    : transitionState === 'resuming' ? 'Resuming…' : transitionState.replace('_', ' ');

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
    // resolve to the existing colours under light/dark (no visual change).
    // `toolbar` is the design-handoff structural class — a theme can
    // attach a border treatment via `:root[data-theme="..."] .toolbar`.
    <div className="toolbar flex h-12 items-center justify-between gap-3 border-b border-border-default bg-bg-panel px-3">
      {/* ---------- Left Section ---------- */}
      {/* Design-handoff layout contract: every toolbar cluster is pinned
          (shrink-0) except the workflow-name chip, which is the ONE
          shrinkable group. Wide-tracking themes (uppercase display type)
          widen every label; without pinning, the mode toggle and action
          cluster collapse before the name does. */}
      <div className="flex shrink-0 items-center gap-1.5">
        <Button
          variant="quiet"
          size="icon-sm"
          onClick={onToggleSidebar}
          aria-pressed={sidebarVisible}
          title={sidebarVisible ? 'Hide sidebar' : 'Show sidebar'}
          className={NEUTRAL_TOGGLE}
        >
          {sidebarVisible ? <PanelLeftClose /> : <PanelLeftOpen />}
        </Button>

        <Divider />

        {/* File menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="quiet" size="sm" className={NEUTRAL}>
              <FileText />
              {/* font-display + tracking-display drive the per-theme display
                  font + letter-spacing; uppercase is gated by --type-uppercase
                  via the new-contract `tracking-display` token (light/dark
                  use 0 + none). */}
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
            <DropdownMenuItem onSelect={onNew}>
              <FilePlus />
              New Workflow
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onOpen}>
              <FolderOpen />
              Open
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onExportFile}>
              <Download />
              Export
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onImportJSON}>
              <Upload />
              Import
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onExportJSON}>
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
                light/dark the workflow name reads as our regular sans-serif. */}
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
          <span className="text-sm font-semibold whitespace-nowrap text-fg-muted">
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
            <Button
              variant="quiet"
              onClick={() => globalModelState.global_provider && globalModelState.global_model && onOverrideAllAgents?.(globalModelState.global_provider, globalModelState.global_model)}
              title="Override all agent nodes in this workflow to use the selected model"
              className={cn(NEUTRAL_ACTION, 'px-3 text-xs')}
            >
              <Repeat className="size-3" />
              Apply All
            </Button>
          )}
        </div>
      )}

      {/* ---------- Right Section (pinned: mode toggle + actions + save state) ---------- */}
      <div className="flex shrink-0 items-center gap-1.5">
        {/* Mode Toggle - segmented control. With Normal mode on it switches
            screens (Home / this editor); otherwise it filters the palette. */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-fg-muted">Mode:</span>
          {featureFlags.normalMode ? <ModeToggle /> : <div
            className="flex items-center rounded-md border border-border bg-card p-0.5"
            title={proMode ? 'Dev mode: All components visible' : 'Normal mode: Only AI components'}
          >
            <button
              onClick={() => proMode && onToggleProMode()}
              className={cn(
                'flex items-center gap-1 rounded-sm border px-2.5 py-1 text-xs font-semibold transition-all',
                !proMode
                  ? 'border-border-default bg-bg-elevated text-fg-default shadow-card cursor-default'
                  : 'cursor-pointer border-transparent text-fg-muted hover:bg-bg-hover'
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
                  ? 'border-border-default bg-bg-elevated text-fg-default shadow-card cursor-default'
                  : 'cursor-pointer border-transparent text-fg-muted hover:bg-bg-hover'
              )}
            >
              <Zap className="h-3 w-3" />
              Dev
            </button>
          </div>}
        </div>

        <Divider />

        <Button variant="quiet" size="icon-sm" onClick={onOpenSettings} title="Settings" className={NEUTRAL}>
          <SettingsIcon />
        </Button>

        <Button variant="quiet" size="icon-sm" onClick={onOpenCredentials} title="API Credentials" className={NEUTRAL}>
          <KeyRound />
        </Button>

        <ThemeButton size="icon-sm" className={NEUTRAL} />

        {user && (
          <Button variant="quiet" size="icon-sm" onClick={logout} title={`Logout ${user.display_name}`} className={NEUTRAL}>
            <LogOut />
          </Button>
        )}

        <Divider />

        {/* Durable workflow lifecycle */}
        {hasPendingControlMutation ? (
          <Button variant="quiet" disabled title={`Workflow is ${controlTransitionLabel}`} className={NEUTRAL_ACTION}>
            <LoaderCircle className="size-3 animate-spin" />
            {controlTransitionLabel}
          </Button>
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
          <Button
            variant="quiet"
            onClick={onPauseWorkflow}
            title={workflowControl.state === 'pausing'
              ? 'Check progress of the Stop request'
              : 'Stop after current work finishes; Resume continues from the next pending action'}
            className={NEUTRAL_ACTION}
          >
            <Pause className="size-3 fill-current" />
            {workflowControl.state === 'pausing' ? 'Stopping…' : 'Stop'}
          </Button>
        ) : isAuthoritativeTransition ? (
          <Button variant="quiet" disabled title={`Workflow is ${controlTransitionLabel}`} className={NEUTRAL_ACTION}>
            <LoaderCircle className="size-3 animate-spin" />
            {controlTransitionLabel}
          </Button>
        ) : (
          <Button variant="quiet" disabled title={`Workflow is ${workflowControl.state}`} className={NEUTRAL_ACTION}>
            {workflowControl.state.replace('_', ' ')}
          </Button>
        )}

        {workflowControl.can_reset && (
          <Button
            variant="quiet"
            onClick={() => setResetOpen(true)}
            disabled={resetBlocked}
            title={isRetryingReset
              ? 'Retry the interrupted reset transition'
              : 'Stop active work and reset this workflow'}
            className={NEUTRAL_ACTION}
          >
            <RotateCcw className="size-3" />
            {isRetryingReset ? 'Retry Reset' : 'Reset'}
          </Button>
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

        <AlertDialog open={resetOpen} onOpenChange={setResetOpen}><AlertDialogContent><AlertDialogHeader><AlertDialogTitle>Reset workflow execution?</AlertDialogTitle><AlertDialogDescription>This stops active work, cancels phone tasks, and archives the current generation. Reset waits for phone actions to finish cleaning up. Task history remains available. The workflow will remain stopped until you press Start.</AlertDialogDescription></AlertDialogHeader><AlertDialogFooter><AlertDialogCancel>Keep current execution</AlertDialogCancel><AlertDialogAction disabled={resetBlocked} onClick={onResetWorkflow}>{isRetryingReset ? 'Retry reset' : 'Reset workflow'}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>

        <Button
          variant="quiet"
          onClick={() => typeof onSave === 'function' && onSave()}
          disabled={!hasUnsavedChanges}
          title={hasUnsavedChanges ? 'Save changes' : 'No changes to save'}
          className={NEUTRAL_ACTION}
        >
          <Save className="size-3" />
          Save
        </Button>

        {/* Status Indicator — font-mono tracks the new-contract --font-mono
            (the system mono under light/dark). */}
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
          variant="quiet"
          size="icon-sm"
          onClick={onToggleComponentPalette}
          aria-pressed={componentPaletteVisible}
          title={componentPaletteVisible ? 'Hide components' : 'Show components'}
          className={NEUTRAL_TOGGLE}
        >
          {componentPaletteVisible ? <PanelRightClose /> : <PanelRightOpen />}
        </Button>

        <Button
          variant="quiet"
          size="icon-sm"
          onClick={toggleCanvasDock}
          aria-pressed={canvasDockOpen}
          title={canvasDockOpen ? 'Hide workspace' : 'Show workspace'}
          aria-label="Workspace"
          className={NEUTRAL_TOGGLE}
        >
          <Monitor />
        </Button>
      </div>
    </div>
  );
};

export default TopToolbar;
