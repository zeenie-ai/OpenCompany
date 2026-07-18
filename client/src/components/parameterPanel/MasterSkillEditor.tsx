/**
 * MasterSkillEditor - Editor for Master Skill node
 *
 * Split panel: left side has folder input, search, and skill toggles.
 * Right side shows selected skill's markdown instructions.
 *
 * Skills loaded from skillFolder (server/skills/<folder>/) or built-in list.
 * The skillsConfig uses skillName (folder name) as keys.
 *
 * User skills are created/edited inline in the right panel (no modal).
 */

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Loader2, Info, Plus, Trash2, Save, X, RotateCcw, Search, Folder, Inbox } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge as DSBadge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

import { ActionButton } from '@/components/ui/action-button';
import { Alert as DSAlert, AlertDescription } from '@/components/ui/alert';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { toast } from 'sonner';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { NodeIcon } from '../../assets/icons';
import { cn } from '@/lib/utils';
import { useFolderSkills } from '../../hooks/useFolderSkills';
import { useNodeAllowlist } from '../../hooks/useNodeAllowlist';

// Skill configuration stored in node parameters
// Key is skillName (folder name like 'whatsapp-skill')
interface SkillConfig {
  enabled: boolean;
  instructions: string;
  isCustomized: boolean;
}

interface MasterSkillConfig {
  [skillName: string]: SkillConfig;
}

interface AvailableSkill {
  type: string;        // Node type (e.g., 'whatsappSkill')
  skillName: string;   // Skill folder name (e.g., 'whatsapp-skill') - used as config key
  displayName: string;
  icon: string;
  color: string;
  description: string;
  isUserSkill?: boolean;  // True if this is a user-created skill from database
}

// User skill from database
interface UserSkill {
  name: string;
  display_name: string;
  description: string;
  instructions: string;
  icon: string;
  color: string;
  category: string;
  is_active: boolean;
}

// Pending skill data for create/edit
interface PendingSkillData {
  name: string;
  display_name: string;
  description: string;
  instructions: string;
  icon: string;
  color: string;
}

// Stable empty-array references. `query.data ?? []` mints a NEW array
// identity every render, and the folder query is disabled (so `data`
// stays undefined) whenever the node's `skillFolder` is unset. Without
// a stable fallback the `availableSkills` memo recomputes on every
// render, and the "load selected user skill" effect setStates in an
// unbounded loop -> "Maximum update depth exceeded" -> the ErrorBoundary
// tears down the React Flow canvas.
const EMPTY_USER_SKILLS: UserSkill[] = [];
const EMPTY_FOLDER_SKILLS: AvailableSkill[] = [];

interface MasterSkillEditorProps {
  skillsConfig: MasterSkillConfig;
  onConfigChange: (config: MasterSkillConfig) => void;
  skillFolder?: string;
  onSkillFolderChange?: (folder: string) => void;
  nodeId?: string;  // For persisting skillsConfig to database
}

// All skill-icon rendering routes through the shared <NodeIcon>
// primitive — see `assets/icons/NodeIcon.tsx`. Sizing comes from
// the wrapper's Tailwind classes (`h-4 w-4 text-base`); brand color
// flows via `color` and is picked up by lucide icons through
// currentColor (image / lobehub `.Color` icons ignore it).

const MasterSkillEditor: React.FC<MasterSkillEditorProps> = ({
  skillsConfig,
  onConfigChange,
  skillFolder,
  onSkillFolderChange,
  nodeId
}) => {
  const { sendRequest } = useWebSocket();
  const [selectedSkillName, setSelectedSkillName] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const editorWrapperRef = useRef<HTMLDivElement>(null);
  // Tracks which skill's data has been loaded into the edit buffer so
  // the load effect below fires once per selection instead of on every
  // background list refetch (avoids the render loop + clobbering edits).
  const initializedSkillRef = useRef<string | null>(null);

  const queryClient = useQueryClient();
  const userSkillsQuery = useQuery<UserSkill[], Error>({
    queryKey: ['userSkills'],
    queryFn: async () => {
      const response = await sendRequest<{ skills: UserSkill[]; count: number }>(
        'get_user_skills',
        { active_only: false },
      );
      return response?.skills ?? [];
    },
    staleTime: 60_000,
  });
  const userSkills = userSkillsQuery.data ?? EMPTY_USER_SKILLS;
  const invalidateUserSkills = useCallback(
    () => queryClient.invalidateQueries({ queryKey: ['userSkills'] }),
    [queryClient],
  );

  const foldersQuery = useQuery<Array<{ name: string; skill_count: number }>, Error>({
    queryKey: ['skillFolders'],
    queryFn: async () => {
      const response = await sendRequest<{
        success: boolean;
        folders: Array<{ name: string; skill_count: number }>;
      }>('list_skill_folders', {});
      return response?.success ? (response.folders ?? []) : [];
    },
    staleTime: Infinity,
  });

  // Filter out skill folders disabled via the allowlist
  // (server/config/node_allowlist.json -> disabled_skill_folders).
  // Same mode-independent enforcement as the credential-category and
  // node-group blocklists. Use to auto-hide skill folders tied to a
  // disabled feature (e.g. `android_agent` when android nodes +
  // credentials are blocked).
  const { isSkillFolderDisabled } = useNodeAllowlist();
  const availableFolders = useMemo(
    () => (foldersQuery.data ?? []).filter((f) => !isSkillFolderDisabled(f.name)),
    [foldersQuery.data, isSkillFolderDisabled],
  );
  const foldersLoaded = !foldersQuery.isLoading;

  const folderSkillsQuery = useFolderSkills(skillFolder);
  const folderSkills = folderSkillsQuery.data ?? EMPTY_FOLDER_SKILLS;
  const folderLoading = folderSkillsQuery.isLoading;

  // Inline editing state (no modal)
  const [isCreatingNew, setIsCreatingNew] = useState(false);
  const [pendingSkillData, setPendingSkillData] = useState<PendingSkillData | null>(null);
  const [savingSkill, setSavingSkill] = useState(false);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

  // Native DOM keydown stops React Flow's document listener from eating Ctrl+A/C/etc.
  useEffect(() => {
    const el = editorWrapperRef.current;
    if (!el) return;

    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.stopPropagation();
      }
    };
    el.addEventListener('keydown', handler);
    return () => el.removeEventListener('keydown', handler);
  });

  // Build list of available skills - from folder scan, node definitions, and user skills
  const availableSkills = useMemo<AvailableSkill[]>(() => {
    const skills: AvailableSkill[] = [];

    // Add folder skills (backend-sourced). The legacy fallback to
    // built-in skill nodeDefinitions has been removed — SKILL_NODE_TYPES
    // was ['masterSkill'] and the filter always returned an empty list.
    if (skillFolder && folderSkills.length > 0) {
      skills.push(...folderSkills);
    }

    // Add user-created skills
    userSkills.forEach(us => {
      skills.push({
        type: 'userSkill',
        skillName: us.name,
        displayName: us.display_name,
        icon: us.icon || '',
        color: us.color || '#6366F1',
        description: us.description || '',
        isUserSkill: true
      });
    });

    return skills;
  }, [skillFolder, folderSkills, userSkills]);

  // Filter skills based on search query
  const filteredSkills = useMemo(() => {
    if (!searchQuery.trim()) return availableSkills;
    const query = searchQuery.toLowerCase();
    return availableSkills.filter(skill =>
      skill.displayName.toLowerCase().includes(query) ||
      skill.description.toLowerCase().includes(query)
    );
  }, [availableSkills, searchQuery]);


  // Auto-select first skill
  useEffect(() => {
    if (!selectedSkillName && !isCreatingNew && availableSkills.length > 0) {
      setSelectedSkillName(availableSkills[0].skillName);
    }
  }, [selectedSkillName, isCreatingNew, availableSkills]);

  const fetchSkillContent = useCallback(async (skillName: string): Promise<string> => {
    try {
      setIsLoading(true);
      const content = await queryClient.fetchQuery<string>({
        queryKey: ['skillContent', skillName],
        queryFn: async () => {
          const response = await sendRequest<{ instructions: string; success: boolean; error?: string }>(
            'get_skill_content',
            { skill_name: skillName },
          );
          if (response?.success && response.instructions) return response.instructions;
          console.warn('[MasterSkillEditor] No content returned for skill:', skillName, response?.error);
          return '';
        },
        staleTime: Infinity,
      });
      return content;
    } catch (error) {
      console.error('[MasterSkillEditor] Failed to load skill content:', error);
      return '';
    } finally {
      setIsLoading(false);
    }
  }, [queryClient, sendRequest]);

  const getCachedSkillContent = useCallback(
    (skillName: string): string | undefined => queryClient.getQueryData<string>(['skillContent', skillName]),
    [queryClient],
  );

  useEffect(() => {
    if (!selectedSkillName || isCreatingNew) return;
    const skillExists = availableSkills.some(s => s.skillName === selectedSkillName);
    if (!skillExists) return;
    if (getCachedSkillContent(selectedSkillName) !== undefined) return;
    fetchSkillContent(selectedSkillName);
  }, [selectedSkillName, isCreatingNew, fetchSkillContent, getCachedSkillContent, availableSkills]);

  // Background prefetch: as soon as the folder's skills are known,
  // queue up `get_skill_content` for each in parallel so the per-skill
  // round-trip is already cached when the user clicks a checkbox.
  // Without this, the first toggle of each skill awaits the WS call
  // inside handleToggleSkill, which the user perceives as "slow toggle"
  // -- the checkbox visually lags by the network round-trip.
  // queryClient.prefetchQuery is a no-op when the data is already
  // cached, so subsequent panel opens are free.
  useEffect(() => {
    if (availableSkills.length === 0) return;
    for (const skill of availableSkills) {
      if (skill.isUserSkill) continue;
      if (getCachedSkillContent(skill.skillName) !== undefined) continue;
      queryClient.prefetchQuery({
        queryKey: ['skillContent', skill.skillName],
        queryFn: async () => {
          const response = await sendRequest<{
            instructions: string;
            success: boolean;
            error?: string;
          }>('get_skill_content', { skill_name: skill.skillName });
          return (response?.success && response.instructions) || '';
        },
        staleTime: Infinity,
      });
    }
  }, [availableSkills, queryClient, sendRequest, getCachedSkillContent]);

  // When selecting a user skill, load its data into pendingSkillData for editing.
  // Guarded by initializedSkillRef so it runs ONCE per selection -- re-running on
  // every availableSkills/userSkills refetch would (a) clobber in-progress edits
  // and (b) drive an infinite setState loop (setPendingSkillData with a fresh
  // object every render). The ref is only advanced once the skill's data is
  // actually present, so a selection made before its data loads still resolves.
  useEffect(() => {
    if (isCreatingNew || !selectedSkillName) {
      initializedSkillRef.current = null;
      return;
    }
    if (initializedSkillRef.current === selectedSkillName) return;

    const selectedInfo = availableSkills.find(s => s.skillName === selectedSkillName);
    if (!selectedInfo) return; // not in the list yet -- retry when it loads

    if (selectedInfo.isUserSkill) {
      const userSkill = userSkills.find(us => us.name === selectedSkillName);
      if (!userSkill) return; // user-skill data not loaded yet -- retry
      setPendingSkillData({
        name: userSkill.name,
        display_name: userSkill.display_name,
        description: userSkill.description,
        instructions: userSkill.instructions,
        icon: userSkill.icon || '',
        color: userSkill.color || '#6366F1'
      });
      setHasUnsavedChanges(false);
    } else {
      setPendingSkillData(null);
      setHasUnsavedChanges(false);
    }
    initializedSkillRef.current = selectedSkillName;
  }, [selectedSkillName, isCreatingNew, availableSkills, userSkills]);

  // Toggle skill enabled/disabled
  const handleToggleSkill = useCallback(async (skillName: string, enabled: boolean) => {
    const currentConfig = skillsConfig[skillName];

    if (enabled && !currentConfig?.instructions) {
      // Load default instructions when enabling for first time
      const defaultContent = await fetchSkillContent(skillName);
      onConfigChange({
        ...skillsConfig,
        [skillName]: { enabled: true, instructions: defaultContent, isCustomized: false }
      });
    } else {
      onConfigChange({
        ...skillsConfig,
        [skillName]: {
          enabled,
          instructions: currentConfig?.instructions || '',
          isCustomized: currentConfig?.isCustomized || false
        }
      });
    }
  }, [skillsConfig, onConfigChange, fetchSkillContent]);

  const handleUpdateInstructions = useCallback((skillName: string, instructions: string) => {
    const currentConfig = skillsConfig[skillName];
    const defaultContent = getCachedSkillContent(skillName) ?? '';
    const isCustomized = instructions !== defaultContent;

    onConfigChange({
      ...skillsConfig,
      [skillName]: {
        enabled: currentConfig?.enabled || false,
        instructions,
        isCustomized
      }
    });
  }, [skillsConfig, onConfigChange, getCachedSkillContent]);

  const handleResetToDefault = useCallback(async (skillName: string) => {
    queryClient.removeQueries({ queryKey: ['skillContent', skillName] });
    const defaultContent = await fetchSkillContent(skillName);
    const currentConfig = skillsConfig[skillName];

    onConfigChange({
      ...skillsConfig,
      [skillName]: { enabled: currentConfig?.enabled || false, instructions: defaultContent, isCustomized: false }
    });
  }, [skillsConfig, onConfigChange, fetchSkillContent, queryClient]);

  // Create new skill - show inline editor
  const handleCreateSkill = useCallback(() => {
    setIsCreatingNew(true);
    setSelectedSkillName(null);
    setPendingSkillData({
      name: '',
      display_name: '',
      description: '',
      instructions: '# Skill Instructions\n\nDescribe what this skill does and how the AI should use it.',
      icon: '',
      color: '#6366F1'
    });
    setHasUnsavedChanges(false);
  }, []);

  // Cancel creating new skill
  const handleCancelCreate = useCallback(() => {
    setIsCreatingNew(false);
    setPendingSkillData(null);
    setHasUnsavedChanges(false);
    // Select first available skill
    if (availableSkills.length > 0) {
      setSelectedSkillName(availableSkills[0].skillName);
    }
  }, [availableSkills]);

  // Update pending skill data
  const handlePendingDataChange = useCallback((field: keyof PendingSkillData, value: string) => {
    setPendingSkillData(prev => prev ? { ...prev, [field]: value } : null);
    setHasUnsavedChanges(true);
  }, []);

  // Save skill (create or update)
  const handleSaveSkill = useCallback(async () => {
    if (!pendingSkillData) return;

    // Validate required fields
    if (!pendingSkillData.display_name.trim()) {
      toast.error('Display name is required');
      return;
    }
    if (!pendingSkillData.instructions.trim()) {
      toast.error('Instructions are required');
      return;
    }

    // For new skills, generate name from display_name if not provided
    let skillName = pendingSkillData.name;
    if (isCreatingNew) {
      if (!skillName.trim()) {
        skillName = pendingSkillData.display_name
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/^-|-$/g, '');
      }
      if (!skillName || !/^[a-z0-9-]+$/.test(skillName)) {
        toast.error('Invalid skill ID. Use lowercase letters, numbers, and hyphens only.');
        return;
      }
    }

    setSavingSkill(true);
    try {
      const handler = isCreatingNew ? 'create_user_skill' : 'update_user_skill';
      const payload = {
        name: skillName,
        display_name: pendingSkillData.display_name,
        description: pendingSkillData.description,
        instructions: pendingSkillData.instructions,
        icon: pendingSkillData.icon,
        color: pendingSkillData.color,
        category: skillFolder || 'custom',
        is_active: true
      };

      const result = await sendRequest<{ skill?: UserSkill; success?: boolean; error?: string }>(handler, payload);

      if (result.skill || result.success) {
        toast.success(isCreatingNew ? 'Skill created' : 'Skill saved');
        await invalidateUserSkills();

        if (isCreatingNew) {
          // Add to config as enabled, then persist to the database so the
          // new skill survives panel close / reload -- mirrors
          // handleDeleteSkill. Without the save_node_parameters call the
          // enabled state lives only in React state and is lost unless the
          // user also clicks the panel-level Save.
          const newConfig = {
            ...skillsConfig,
            [skillName]: { enabled: true, instructions: pendingSkillData.instructions, isCustomized: false }
          };
          onConfigChange(newConfig);
          if (nodeId) {
            await sendRequest('save_node_parameters', {
              node_id: nodeId,
              parameters: { skills_config: newConfig, skillFolder: skillFolder || 'assistant' }
            });
          }
          setIsCreatingNew(false);
          setSelectedSkillName(skillName);
        }
        setHasUnsavedChanges(false);
      } else {
        toast.error(result.error || 'Failed to save skill');
      }
    } catch (err: any) {
      toast.error(err.message || 'Failed to save skill');
    } finally {
      setSavingSkill(false);
    }
  }, [pendingSkillData, isCreatingNew, skillFolder, sendRequest, invalidateUserSkills, skillsConfig, onConfigChange, nodeId]);

  // Delete user skill
  const handleDeleteSkill = useCallback(async (skillName: string) => {
    try {
      const result = await sendRequest<{ success: boolean; error?: string }>('delete_user_skill', { name: skillName });
      if (result.success) {
        toast.success('Skill deleted');
        // Remove from config
        const newConfig = { ...skillsConfig };
        delete newConfig[skillName];
        onConfigChange(newConfig);

        // Persist cleaned config to database so deleted skill doesn't reappear
        if (nodeId) {
          await sendRequest('save_node_parameters', {
            node_id: nodeId,
            parameters: { skills_config: newConfig, skillFolder: skillFolder || 'assistant' }
          });
        }

        // Clear selection if deleted skill was selected
        if (selectedSkillName === skillName) {
          setSelectedSkillName(null);
          setPendingSkillData(null);
        }
        // Refresh user skills list from database
        await invalidateUserSkills();
      } else {
        toast.error(result.error || 'Failed to delete skill');
      }
    } catch (err: any) {
      toast.error(err.message || 'Failed to delete skill');
    }
  }, [sendRequest, invalidateUserSkills, skillsConfig, onConfigChange, selectedSkillName, nodeId, skillFolder]);

  const selectedSkillInfo = availableSkills.find(s => s.skillName === selectedSkillName);
  const selectedSkillConfig = selectedSkillName ? skillsConfig[selectedSkillName] : undefined;
  const enabledCount = Object.values(skillsConfig).filter(c => c?.enabled).length;
  const isEditingUserSkill = selectedSkillInfo?.isUserSkill && pendingSkillData;

  return (
    <div className="flex flex-1 min-h-0 gap-3 overflow-hidden">
      {/* Left Panel - Skills List */}
      <div className="flex flex-[0_0_260px] flex-col overflow-hidden rounded-md border border-border-default bg-bg-elevated">
        {/* Header with count and create button */}
        <div className="flex items-center justify-between border-b border-border-default bg-bg-panel p-2">
          <span className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-sm font-semibold text-fg-default">
            Skills
          </span>
          <div className="flex items-center gap-1">
            <DSBadge className="h-5 min-w-5 justify-center rounded-full bg-action-tools px-1.5 text-white">
              {enabledCount}
            </DSBadge>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <ActionButton
                    intent="tools"
                    onClick={handleCreateSkill}
                    disabled={isCreatingNew}
                    className="h-7 px-2"
                    aria-label="Create new skill"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </ActionButton>
                </TooltipTrigger>
                <TooltipContent>Create new skill</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </div>

        {/* Search */}
        <div className="border-b border-border p-2">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search skills..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-9 pl-8 pr-8"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                aria-label="Clear search"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* Skill Folder Dropdown */}
        <div className="shrink-0 border-b border-border p-2">
          <Select
            value={skillFolder || 'assistant'}
            onValueChange={(value) => {
              onSkillFolderChange?.(value);
              setSelectedSkillName(null);
            }}
            disabled={!foldersLoaded}
          >
            <SelectTrigger className="w-full">
              <div className="flex items-center gap-2">
                <Folder className="h-4 w-4 text-muted-foreground" />
                <SelectValue placeholder="Choose folder" />
              </div>
            </SelectTrigger>
            <SelectContent>
              {availableFolders.map((f) => (
                <SelectItem key={f.name} value={f.name}>
                  {f.name} ({f.skill_count})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Skills List */}
        <div className="flex-1 overflow-y-auto">
          {folderLoading ? (
            <div className="mt-4 flex items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Scanning folder...
            </div>
          ) : filteredSkills.length === 0 ? (
            <div className="mt-6 flex flex-col items-center gap-2 p-6 text-center text-sm text-muted-foreground">
              <Inbox className="h-10 w-10 opacity-50" />
              <p>{skillFolder ? `No skills found in skills/${skillFolder}/` : 'No skills found'}</p>
            </div>
          ) : (
            <div className="divide-y divide-border">
              {filteredSkills.map((skill) => {
                const config = skillsConfig[skill.skillName];
                const isSelected = selectedSkillName === skill.skillName && !isCreatingNew;
                const isEnabled = config?.enabled || false;
                const isCustomized = config?.isCustomized || false;

                return (
                  <div
                    key={skill.skillName}
                    onClick={() => {
                      if (isCreatingNew) {
                        if (hasUnsavedChanges) {
                          if (!confirm('Discard unsaved changes?')) return;
                        }
                        setIsCreatingNew(false);
                      }
                      setSelectedSkillName(skill.skillName);
                    }}
                    className={cn(
                      'cursor-pointer border-l-[3px] px-3 py-2 transition-colors',
                      isSelected ? 'bg-tint-soft' : 'border-l-transparent',
                    )}
                    // currentColor is the skill's brand color when
                    // selected; `bg-tint-soft` mixes it at the
                    // canonical alpha (--tint-soft) and the left
                    // border picks up the same color.
                    style={isSelected ? { color: skill.color, borderLeftColor: skill.color } : undefined}
                  >
                    <div className="flex w-full items-center gap-2">
                      <Checkbox
                        checked={isEnabled}
                        onCheckedChange={(checked) => {
                          handleToggleSkill(skill.skillName, checked === true);
                        }}
                        onClick={(e) => e.stopPropagation()}
                      />
                      <NodeIcon icon={skill.icon} className="h-4 w-4 text-base" />
                      <span
                        className={cn(
                          'flex-1 overflow-hidden text-sm whitespace-nowrap text-ellipsis',
                          isSelected ? 'font-semibold' : 'font-medium',
                          isEnabled ? 'text-fg-default' : 'text-fg-muted',
                        )}
                      >
                        {skill.displayName}
                      </span>
                      {isCustomized && (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <div className="h-2 w-2 shrink-0 rounded-full bg-warning" />
                            </TooltipTrigger>
                            <TooltipContent>Customized</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      )}
                      {skill.isUserSkill && (
                        <DSBadge className="h-4 bg-info/20 px-1 text-[10px] text-info">
                          Custom
                        </DSBadge>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Right Panel - Skill Editor */}
      <div className="flex flex-1 flex-col overflow-hidden rounded-md border border-border-default bg-bg-elevated">
        {/* Creating new skill */}
        {isCreatingNew && pendingSkillData ? (
          <>
            {/* New Skill Header */}
            <div className="flex items-center gap-3 border-b border-border-default bg-bg-panel p-3">
              <div
                className="flex h-10 w-10 items-center justify-center rounded-md border-2 border-dashed bg-tint-soft"
                // currentColor is the new skill's brand color;
                // `bg-tint-soft` mixes it against transparent at the
                // canonical alpha (--tint-soft); the dashed border
                // and inner glyph pick up the same color.
                style={{ color: pendingSkillData.color }}
              >
                {pendingSkillData.icon
                  ? <NodeIcon icon={pendingSkillData.icon} className="h-5 w-5 text-xl" />
                  : <Plus className="h-5 w-5" />}
              </div>
              <div className="flex-1">
                <div className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] text-sm font-semibold text-success">
                  Create New Skill
                </div>
                <div className="mt-0.5 text-xs text-fg-muted">
                  Fill in the details below and save
                </div>
              </div>
              <ActionButton intent="stop" onClick={handleCancelCreate} className="h-8">
                <X className="h-3.5 w-3.5" />
                Cancel
              </ActionButton>
              <ActionButton
                intent="save"
                onClick={handleSaveSkill}
                disabled={savingSkill}
                className="h-8"
              >
                {savingSkill ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                Save
              </ActionButton>
            </div>

            {/* New Skill Form */}
            <div
              ref={editorWrapperRef}
              className="flex flex-1 flex-col gap-3 overflow-auto p-3"
            >
              {/* Display Name */}
              <div>
                <label className="mb-1 block text-xs text-fg-muted">
                  Display Name *
                </label>
                <Input
                  value={pendingSkillData.display_name}
                  onChange={(e) => handlePendingDataChange('display_name', e.target.value)}
                  placeholder="My Custom Skill"
                  className="bg-bg-input border-border-default"
                />
              </div>

              {/* Description */}
              <div>
                <label className="mb-1 block text-xs text-fg-muted">
                  Description
                </label>
                <Input
                  value={pendingSkillData.description}
                  onChange={(e) => handlePendingDataChange('description', e.target.value)}
                  placeholder="Brief description of what this skill does"
                  className="bg-bg-input border-border-default"
                />
              </div>

              {/* Icon and Color */}
              <div className="flex gap-3">
                <div className="w-[100px]">
                  <label className="mb-1 block text-xs text-fg-muted">
                    Icon (emoji)
                  </label>
                  <Input
                    value={pendingSkillData.icon}
                    onChange={(e) => handlePendingDataChange('icon', e.target.value)}
                    placeholder=""
                    className="bg-bg-input border-border-default text-center"
                  />
                </div>
                <div className="w-20">
                  <label className="mb-1 block text-xs text-fg-muted">
                    Color
                  </label>
                  <Input
                    type="color"
                    value={pendingSkillData.color}
                    onChange={(e) => handlePendingDataChange('color', e.target.value)}
                    className="h-8 bg-bg-input border-border-default p-0.5"
                  />
                </div>
              </div>

              {/* Instructions */}
              <div className="flex flex-1 flex-col min-h-[200px]">
                <label className="mb-1 block text-xs text-fg-muted">
                  Instructions *
                </label>
                <Textarea
                  value={pendingSkillData.instructions}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => handlePendingDataChange('instructions', e.target.value)}
                  placeholder="# Skill Instructions..."
                  spellCheck={false}
                  className="flex-1 min-h-0 resize-none rounded-md border-border-default bg-bg-input font-mono text-[13px] leading-[1.5] text-fg-default"
                />
              </div>
            </div>
          </>
        ) : selectedSkillInfo ? (
          <>
            {/* Skill Header */}
            <div className="flex items-center gap-3 border-b border-border-default bg-bg-panel p-3">
              <NodeIcon icon={selectedSkillInfo.icon} className="h-6 w-6 text-2xl" />
              <div className="flex-1">
                {isEditingUserSkill ? (
                  <Input
                    value={pendingSkillData?.display_name || ''}
                    onChange={(e) => handlePendingDataChange('display_name', e.target.value)}
                    className="mb-1 bg-bg-elevated border-border-default text-sm font-semibold"
                  />
                ) : (
                  <div className="font-display tracking-[var(--type-tracking-display)] [text-transform:var(--type-uppercase)] flex items-center gap-2 text-sm font-semibold text-fg-default">
                    {selectedSkillInfo.displayName}
                    {selectedSkillConfig?.isCustomized && (
                      <DSBadge className="h-4 bg-warning/20 text-xs text-warning">
                        Customized
                      </DSBadge>
                    )}
                  </div>
                )}
                {isEditingUserSkill ? (
                  <Input
                    value={pendingSkillData?.description || ''}
                    onChange={(e) => handlePendingDataChange('description', e.target.value)}
                    placeholder="Description"
                    className="bg-bg-elevated border-border-default text-xs"
                  />
                ) : (
                  <div className="mt-0.5 text-xs text-fg-muted">
                    {selectedSkillInfo.description}
                  </div>
                )}
              </div>

              {/* User skill actions */}
              {isEditingUserSkill && (
                <>
                  {hasUnsavedChanges && (
                    <ActionButton
                      intent="save"
                      onClick={handleSaveSkill}
                      disabled={savingSkill}
                      className="h-8"
                    >
                      {savingSkill ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                      Save
                    </ActionButton>
                  )}
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <ActionButton intent="stop" className="h-8">
                        <Trash2 className="h-3.5 w-3.5" />
                        Delete
                      </ActionButton>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Delete this skill?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This action cannot be undone. The skill and any custom
                          instructions will be permanently removed.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                          onClick={() => handleDeleteSkill(selectedSkillName!)}
                          className="bg-destructive text-white hover:bg-destructive/90"
                        >
                          Delete
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </>
              )}

              {/* Reset Button for built-in skills */}
              {!isEditingUserSkill && selectedSkillConfig?.isCustomized && (
                <ActionButton
                  intent="config"
                  onClick={() => handleResetToDefault(selectedSkillName!)}
                  disabled={isLoading}
                  className="h-8"
                >
                  {isLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
                  Reset
                </ActionButton>
              )}
            </div>

            {/* Skill Instructions Editor */}
            <div
              ref={editorWrapperRef}
              className="flex flex-1 flex-col overflow-hidden p-3"
            >
              {isLoading ? (
                <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading...
                </div>
              ) : (
                <Textarea
                  value={
                    isEditingUserSkill
                      ? pendingSkillData?.instructions || ''
                      : (selectedSkillConfig?.instructions || getCachedSkillContent(selectedSkillName!) || '')
                  }
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => {
                    if (isEditingUserSkill) {
                      handlePendingDataChange('instructions', e.target.value);
                    } else {
                      handleUpdateInstructions(selectedSkillName!, e.target.value);
                    }
                  }}
                  placeholder="Loading skill instructions..."
                  spellCheck={false}
                  className="flex-1 min-h-0 resize-none rounded-md border-border-default bg-bg-input font-mono text-[13px] leading-[1.5] text-fg-default"
                />
              )}

              {/* Enable hint */}
              {!selectedSkillConfig?.enabled && (
                <DSAlert variant="info" className="mt-3">
                  <Info className="h-4 w-4" />
                  <AlertDescription>
                    Enable this skill to include it when running the AI Agent.
                  </AlertDescription>
                </DSAlert>
              )}
            </div>
          </>
        ) : (
          <div className="m-auto flex flex-col items-center gap-2 p-6 text-center text-sm text-muted-foreground">
            <Inbox className="h-10 w-10 opacity-50" />
            <p>Select a skill to view instructions</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default MasterSkillEditor;
