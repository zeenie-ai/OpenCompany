/**
 * Workflow settings schema.
 *
 * One zod schema owns: type definition (via z.infer), default values
 * (via .default()), runtime validation (numeric ranges), and a single
 * mapper between the camelCase frontend shape and the snake_case
 * server `user_settings` row. Adding or changing a setting now means
 * one edit here, not three.
 */

import { z } from 'zod';

/**
 * Tool Result Limit range in characters, shared by the schema below and the
 * Settings slider so validation and the control cannot drift apart.
 */
export const TOOL_RESULT_MAX_CHARS_RANGE = { min: 10_000, max: 200_000, step: 10_000 } as const;

export const workflowSettingsSchema = z.object({
  autoSave: z.boolean().default(true),
  autoSaveInterval: z.number().int().min(10).max(300).default(30),
  sidebarDefaultOpen: z.boolean().default(true),
  componentPaletteDefaultOpen: z.boolean().default(true),
  consolePanelDefaultOpen: z.boolean().default(false),
  memoryWindowSize: z.number().int().min(1).max(100).default(100),
  compactionRatio: z.number().min(0.05).max(0.99).default(0.8),
  agentRecursionLimit: z.number().int().min(1).max(5000).default(200),
  // Mirrors the server's TOOL_RESULT_MAX_CHARS default.
  toolResultMaxChars: z
    .number()
    .int()
    .min(TOOL_RESULT_MAX_CHARS_RANGE.min)
    .max(TOOL_RESULT_MAX_CHARS_RANGE.max)
    .default(100_000),
  maxProcesses: z.number().int().min(1).max(50).default(10),
  autoAddSkillForTools: z.boolean().default(true),
  autoRebindToolsAfterCanvasChange: z.boolean().default(true),
  // The owner-wide Generated UI switch, the same one as Home > Settings > Profile.
  generatedUi: z.boolean().default(false),
});

export type WorkflowSettings = z.infer<typeof workflowSettingsSchema>;

export const defaultSettings: WorkflowSettings = workflowSettingsSchema.parse({});

/**
 * Map a partial server `user_settings` row (snake_case) into
 * WorkflowSettings (camelCase), defaulting any missing field.
 */
export function fromServerRow(row: Record<string, any> | null | undefined): WorkflowSettings {
  if (!row) return defaultSettings;
  return workflowSettingsSchema.parse({
    autoSave: row.auto_save,
    autoSaveInterval: row.auto_save_interval,
    sidebarDefaultOpen: row.sidebar_default_open,
    componentPaletteDefaultOpen: row.component_palette_default_open,
    consolePanelDefaultOpen: row.console_panel_default_open,
    memoryWindowSize: row.memory_window_size,
    compactionRatio: row.compaction_ratio,
    maxProcesses: row.max_processes,
    autoAddSkillForTools: row.auto_add_skill_for_tools,
    autoRebindToolsAfterCanvasChange: row.auto_rebind_tools_after_canvas_change,
    agentRecursionLimit: row.agent_recursion_limit,
    toolResultMaxChars: row.tool_result_max_chars,
    generatedUi: row.generated_ui,
  });
}

/**
 * The server keys that differ between two copies: a change, and Save, send
 * only these, so a value another window saved meanwhile (Home's Profile shows
 * Generated UI too) is never written back from a stale copy.
 */
export function changedRow(previous: WorkflowSettings, next: WorkflowSettings): Record<string, any> {
  const before = toServerRow(previous);
  return Object.fromEntries(Object.entries(toServerRow(next)).filter(([key, value]) => before[key] !== value));
}

/**
 * Map WorkflowSettings (camelCase) back into the snake_case server row
 * shape used by save_user_settings.
 */
export function toServerRow(s: WorkflowSettings): Record<string, any> {
  return {
    auto_save: s.autoSave,
    auto_save_interval: s.autoSaveInterval,
    sidebar_default_open: s.sidebarDefaultOpen,
    component_palette_default_open: s.componentPaletteDefaultOpen,
    console_panel_default_open: s.consolePanelDefaultOpen,
    memory_window_size: s.memoryWindowSize,
    compaction_ratio: s.compactionRatio,
    max_processes: s.maxProcesses,
    auto_add_skill_for_tools: s.autoAddSkillForTools,
    auto_rebind_tools_after_canvas_change: s.autoRebindToolsAfterCanvasChange,
    agent_recursion_limit: s.agentRecursionLimit,
    tool_result_max_chars: s.toolResultMaxChars,
    generated_ui: s.generatedUi,
  };
}
