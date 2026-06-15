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
  Square,
  Repeat,
  Clock,
  Zap,
} from 'lucide-react';
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
import { ThemeSwitcher } from '@/components/ui/ThemeSwitcher';
import { cn } from '@/lib/utils';
import { useAuth } from '../../contexts/AuthContext';
import { useApiKeys, GlobalModelState } from '../../hooks/useApiKeys';
import { useStoredProviderCount } from '../../hooks/useCatalogueQuery';
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
  isRunning?: boolean;
  onDeploy: () => void;
  onCancelDeployment: () => void;
  isDeploying?: boolean;
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
  isRunning = false,
  onDeploy,
  onCancelDeployment,
  isDeploying = false,
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
  const [tempName, setTempName] = useState(workflowName);
  const { user, logout } = useAuth();

  // Global Model Selector state
  const { getValidatedAiProviders, saveGlobalModel, isConnected: apiKeysConnected } = useApiKeys();
  const [globalModelState, setGlobalModelState] = useState<GlobalModelState>({ providers: [], global_provider: null, global_model: null });

  // Re-fetch validated providers whenever the count of stored
  // credentials changes. Read from the catalogue (single source of
  // truth — `provider.stored` flag); the retired
  // `apiKeyStatuses[id].hasKey` mirror duplicated this answer.
  const apiKeyCount = useStoredProviderCount();
  useEffect(() => {
    if (!apiKeysConnected) return;
    getValidatedAiProviders().then(state => setGlobalModelState(state));
  }, [apiKeysConnected, apiKeyCount, getValidatedAiProviders]);

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
      <div className="flex items-center gap-1.5">
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

      {/* ---------- Center: Workflow Name ---------- */}
      <div className="flex flex-1 justify-center">
        {isEditing ? (
          <input
            type="text"
            value={tempName}
            onChange={(e) => setTempName(e.target.value)}
            onBlur={handleNameSubmit}
            onKeyDown={handleNameKeyDown}
            autoFocus
            className="min-w-[200px] rounded-sm border border-accent bg-background px-3 py-1.5 text-center text-sm font-medium text-foreground outline-none"
          />
        ) : (
          <button
            onClick={handleNameClick}
            title="Click to rename"
            className="flex items-center gap-1.5 rounded-sm bg-transparent px-3 py-1.5 transition-colors hover:bg-bg-hover"
          >
            {/* font-display + tracking-display + [text-transform] are
                theme-driven via the new-contract typography tokens. Under
                light/dark the workflow name reads as our regular sans-serif;
                under Renaissance it becomes Cinzel uppercase, under Cyber it
                becomes Major Mono Display uppercase. */}
            <span className="text-sm font-display font-medium tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
              {workflowName}
            </span>
            <Pencil className="h-3 w-3 text-fg-muted" />
          </button>
        )}
      </div>

      {/* ---------- Global Model Selector ---------- */}
      {globalModelState.providers.length > 0 && (
        <div className="flex items-center gap-2">
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
                      {meta?.label || vp.provider}
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

      {/* ---------- Right Section ---------- */}
      <div className="flex items-center gap-1.5">
        {/* Mode Toggle - segmented control */}
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-node-model">Mode:</span>
          <div
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
          </div>
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

        {/* Start / Stop */}
        {!isDeploying ? (
          <ActionButton
            intent="run"
            onClick={onDeploy}
            disabled={isRunning}
            title="Start workflow"
          >
            <Play className="h-3 w-3 fill-current" />
            Start
          </ActionButton>
        ) : (
          <ActionButton
            intent="stop"
            onClick={onCancelDeployment}
            title="Stop workflow"
          >
            <Square className="h-3 w-3 fill-current" />
            Stop
          </ActionButton>
        )}

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
      </div>
    </div>
  );
};

export default TopToolbar;
