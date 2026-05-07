import React, { useEffect } from 'react';
import { toast } from 'sonner';
import {
  Settings as SettingsIcon,
  Monitor,
  Save,
  Brain,
  Cpu,
  HelpCircle,
  RotateCcw,
  X,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ActionButton } from '@/components/ui/action-button';
import { Switch } from '@/components/ui/switch';
import { Slider } from '@/components/ui/slider';
import { Input } from '@/components/ui/input';
import Modal from './Modal';
import {
  useUserSettingsQuery,
  useSaveUserSettingsMutation,
} from '../../hooks/useUserSettingsQuery';
import {
  workflowSettingsSchema,
  defaultSettings,
  fromServerRow,
  toServerRow,
  type WorkflowSettings,
} from './settingsPanel/schema';

export type { WorkflowSettings } from './settingsPanel/schema';
export { defaultSettings } from './settingsPanel/schema';

interface SettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  settings: WorkflowSettings;
  onSettingsChange: (settings: WorkflowSettings) => void;
  onReplayOnboarding?: () => void;
}

// ---------------------------------------------------------------------------
// Reusable row + section primitives
// ---------------------------------------------------------------------------

type SectionTone = 'agent' | 'model' | 'workflow';

const TONE_CLASSES: Record<SectionTone, string> = {
  agent:    'bg-node-agent-soft text-node-agent',
  model:    'bg-node-model-soft text-node-model',
  workflow: 'bg-node-workflow-soft text-node-workflow',
};

interface SectionProps {
  title: string;
  Icon: React.ElementType;
  tone: SectionTone;
  children: React.ReactNode;
}

const Section: React.FC<SectionProps> = ({ title, Icon, tone, children }) => (
  // bg-bg-elevated + border-default — settings sections are elevated
  // cards stacked inside the modal body. font-display + tracking gives
  // Renaissance/Cyber their typographic identity on section headers.
  <div className="mb-4 rounded-md border border-border-default bg-bg-elevated p-4">
    <div className="mb-4 flex items-center gap-2 border-b border-border-default pb-3">
      <div className={`flex h-8 w-8 items-center justify-center rounded-md ${TONE_CLASSES[tone]}`}>
        <Icon className="h-4 w-4" />
      </div>
      <div className="font-display text-base font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
        {title}
      </div>
    </div>
    {children}
  </div>
);

interface RowProps {
  label: string;
  description: string;
  children: React.ReactNode;
}

const Row: React.FC<RowProps> = ({ label, description, children }) => (
  <div className="flex items-center justify-between py-2">
    <div className="flex-1">
      <div className="text-sm font-medium text-fg-default">{label}</div>
      <div className="mt-0.5 text-xs text-fg-muted">{description}</div>
    </div>
    {children}
  </div>
);

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

const SettingsPanel: React.FC<SettingsPanelProps> = ({
  isOpen,
  onClose,
  settings,
  onSettingsChange,
  onReplayOnboarding,
}) => {
  const settingsQuery = useUserSettingsQuery();
  const saveMutation = useSaveUserSettingsMutation();
  const isLoading = settingsQuery.isLoading;
  const isSaving = saveMutation.isPending;

  // Hydrate Dashboard's controlled state from the cached settings row
  // exactly once per open. The query is shared with useOnboarding so
  // cross-component reads stay in sync.
  useEffect(() => {
    if (!isOpen || !settingsQuery.data) return;
    onSettingsChange(fromServerRow(settingsQuery.data));
    // onSettingsChange identity may change every parent render; only
    // re-hydrate when the modal opens or fresh data lands.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, settingsQuery.data]);

  const persist = async (next: WorkflowSettings, withToast: boolean) => {
    // Validate before persisting so an out-of-range field never reaches
    // the server. Surface zod errors as a toast and refuse the save.
    const parsed = workflowSettingsSchema.safeParse(next);
    if (!parsed.success) {
      const message = parsed.error.issues[0]?.message ?? 'Invalid settings';
      toast.error(message);
      return;
    }
    try {
      await saveMutation.mutateAsync(toServerRow(parsed.data));
      if (withToast) toast.success('Settings saved successfully');
    } catch (error) {
      console.error('[SettingsPanel] Failed to save settings:', error);
      if (withToast) toast.error('Failed to save settings');
    }
  };

  const handleChange = (key: keyof WorkflowSettings, value: number | boolean) => {
    const next = { ...settings, [key]: value } as WorkflowSettings;
    onSettingsChange(next);
    void persist(next, false);
  };

  const handleReset = async () => {
    onSettingsChange(defaultSettings);
    await persist(defaultSettings, true);
  };

  const handleSave = async () => {
    await persist(settings, true);
  };

  const headerActions = (
    <div className="flex items-center gap-4">
      <div className="flex items-center gap-2 font-display text-[15px] font-semibold tracking-[var(--type-tracking-display)] text-fg-default [text-transform:var(--type-uppercase)]">
        <SettingsIcon className="h-4 w-4" />
        <span>Settings</span>
      </div>
      <div className="flex items-center gap-2">
        <ActionButton
          intent="config"
          onClick={handleReset}
          disabled={isSaving}
          title="Reset to default settings"
        >
          <RotateCcw className="h-3 w-3" />
          Reset
        </ActionButton>
        <ActionButton
          intent="run"
          onClick={handleSave}
          disabled={isSaving}
          title="Save settings"
        >
          <Save className="h-3 w-3" />
          {isSaving ? 'Saving...' : 'Save'}
        </ActionButton>
        <ActionButton intent="stop" onClick={onClose} title="Close settings">
          <X className="h-3 w-3" />
          Close
        </ActionButton>
      </div>
    </div>
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Settings"
      maxWidth="95vw"
      maxHeight="95vh"
      headerActions={headerActions}
    >
      <div className="flex h-full flex-col">
        <div
          className={`flex-1 overflow-auto p-4 transition-opacity ${isLoading ? 'opacity-60' : 'opacity-100'}`}
        >
          {/* UI Defaults */}
          <Section title="UI Defaults" Icon={Monitor} tone="agent">
            <Row
              label="Sidebar Open by Default"
              description="Show the sidebar panel when the application starts"
            >
              <Switch
                checked={settings.sidebarDefaultOpen}
                onCheckedChange={(checked) => handleChange('sidebarDefaultOpen', checked)}
                disabled={isSaving}
              />
            </Row>
            <Row
              label="Component Palette Open by Default"
              description="Show the component palette when the application starts"
            >
              <Switch
                checked={settings.componentPaletteDefaultOpen}
                onCheckedChange={(checked) => handleChange('componentPaletteDefaultOpen', checked)}
                disabled={isSaving}
              />
            </Row>
            <Row
              label="Console Panel Open by Default"
              description="Show the console/chat panel at the bottom when the application starts"
            >
              <Switch
                checked={settings.consolePanelDefaultOpen}
                onCheckedChange={(checked) => handleChange('consolePanelDefaultOpen', checked)}
                disabled={isSaving}
              />
            </Row>
            <Row
              label="Auto-add Skill for Connected Tools"
              description="When a tool node is connected to an AI agent, automatically enable the matching skill in the agent's Master Skill (creating one if needed). Disconnecting the tool disables the skill."
            >
              <Switch
                checked={settings.autoAddSkillForTools}
                onCheckedChange={(checked) => handleChange('autoAddSkillForTools', checked)}
                disabled={isSaving}
              />
            </Row>
          </Section>

          {/* Auto-save */}
          <Section title="Auto-save" Icon={Save} tone="model">
            <Row
              label="Enable Auto-save"
              description="Automatically save the workflow at regular intervals"
            >
              <Switch
                checked={settings.autoSave}
                onCheckedChange={(checked) => handleChange('autoSave', checked)}
                disabled={isSaving}
              />
            </Row>

            {settings.autoSave && (
              <Row
                label="Auto-save Interval"
                description="How often to auto-save (10-300 seconds)"
              >
                <div className="relative w-24">
                  <Input
                    type="number"
                    min={10}
                    max={300}
                    step={5}
                    value={settings.autoSaveInterval}
                    onChange={(e) => handleChange('autoSaveInterval', Number(e.target.value) || 30)}
                    disabled={isSaving}
                    className="pr-6"
                  />
                  <span className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2 text-xs text-fg-muted">
                    s
                  </span>
                </div>
              </Row>
            )}
          </Section>

          {/* Memory & Compaction */}
          <Section title="Memory & Compaction" Icon={Brain} tone="agent">
            <Row
              label="Default Window Size"
              description="Number of message pairs to keep in short-term memory (1-100)"
            >
              <Input
                type="number"
                min={1}
                max={100}
                step={1}
                value={settings.memoryWindowSize}
                onChange={(e) => handleChange('memoryWindowSize', Number(e.target.value) || 100)}
                disabled={isSaving}
                className="w-20"
              />
            </Row>

            <div className="my-1 border-b border-border-default" />

            <div className="py-2">
              <div className="mb-2 flex items-center justify-between">
                <div className="flex-1">
                  <div className="text-sm font-medium text-fg-default">Compaction Ratio</div>
                  <div className="mt-0.5 text-xs text-fg-muted">
                    Fraction of context window that triggers memory compaction
                  </div>
                </div>
                <span className="min-w-[42px] text-right text-sm font-semibold text-node-model">
                  {Math.round(settings.compactionRatio * 100)}%
                </span>
              </div>
              <Slider
                min={10}
                max={90}
                step={5}
                value={[Math.round(settings.compactionRatio * 100)]}
                onValueChange={(value) => handleChange('compactionRatio', (value[0] ?? 50) / 100)}
                disabled={isSaving}
                className="my-3"
              />
              <div className="flex justify-between text-[10px] text-fg-muted">
                <span>10%</span>
                <span>50%</span>
                <span>90%</span>
              </div>
              <div className="mt-1 text-xs leading-snug text-fg-muted">
                Lower = compact sooner (saves tokens, loses detail). Higher = compact later (preserves context, uses more tokens).
              </div>
            </div>
          </Section>

          {/* Process Manager */}
          <Section title="Process Manager" Icon={Cpu} tone="workflow">
            <Row
              label="Max Concurrent Processes"
              description="Maximum number of running processes per workflow (1-50)"
            >
              <Input
                type="number"
                min={1}
                max={50}
                step={1}
                value={settings.maxProcesses ?? 10}
                onChange={(e) => handleChange('maxProcesses', Number(e.target.value) || 10)}
                disabled={isSaving}
                className="w-20"
              />
            </Row>
          </Section>

          {/* Help */}
          <Section title="Help" Icon={HelpCircle} tone="model">
            <Row
              label="Replay Welcome Guide"
              description="Show the onboarding wizard again to review platform features"
            >
              <Button
                size="sm"
                variant="default"
                onClick={onReplayOnboarding}
                disabled={!onReplayOnboarding}
              >
                <HelpCircle className="h-3.5 w-3.5" />
                Replay
              </Button>
            </Row>
          </Section>
        </div>
      </div>
    </Modal>
  );
};

export default SettingsPanel;
