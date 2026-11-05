import { describe, expect, it } from 'vitest';
import {
  TOOL_RESULT_MAX_CHARS_RANGE,
  changedRow,
  defaultSettings,
  fromServerRow,
  toServerRow,
  workflowSettingsSchema,
} from './schema';

describe('workflow settings schema', () => {
  it('round-trips every setting through the server row', () => {
    const settings = {
      ...defaultSettings,
      compactionRatio: 0.6,
      agentRecursionLimit: 300,
      toolResultMaxChars: 50_000,
    };

    expect(fromServerRow(toServerRow(settings))).toEqual(settings);
  });

  it('maps the tool result limit to the snake_case server key', () => {
    expect(toServerRow({ ...defaultSettings, toolResultMaxChars: 20_000 }).tool_result_max_chars).toBe(20_000);
    expect(fromServerRow({ tool_result_max_chars: 150_000 }).toolResultMaxChars).toBe(150_000);
  });

  it('maps Generated UI both ways and keeps it off for a row saved before it existed', () => {
    expect(toServerRow({ ...defaultSettings, generatedUi: true }).generated_ui).toBe(true);
    expect(fromServerRow({ generated_ui: true }).generatedUi).toBe(true);
    expect(fromServerRow({ compaction_ratio: 0.5 }).generatedUi).toBe(false);
    expect(defaultSettings.generatedUi).toBe(false);
  });

  it('saves only the keys a change touches', () => {
    // A stale copy of Generated UI (Home may have changed it) is not sent back.
    expect(changedRow(defaultSettings, { ...defaultSettings, autoSaveInterval: 45 })).toEqual({ auto_save_interval: 45 });
    expect(changedRow(defaultSettings, { ...defaultSettings, generatedUi: true })).toEqual({ generated_ui: true });
    expect(changedRow(defaultSettings, defaultSettings)).toEqual({});
  });

  it('defaults the tool result limit for a row saved before it existed', () => {
    expect(fromServerRow({ compaction_ratio: 0.5 }).toolResultMaxChars).toBe(defaultSettings.toolResultMaxChars);
  });

  it('rejects a tool result limit outside the slider range', () => {
    const { min, max } = TOOL_RESULT_MAX_CHARS_RANGE;

    for (const toolResultMaxChars of [min - 1, max + 1, 15_000.5]) {
      expect(workflowSettingsSchema.safeParse({ toolResultMaxChars }).success).toBe(false);
    }
  });

  it('puts the default on the slider grid', () => {
    const { min, max, step } = TOOL_RESULT_MAX_CHARS_RANGE;
    const value = defaultSettings.toolResultMaxChars;

    expect(value).toBeGreaterThanOrEqual(min);
    expect(value).toBeLessThanOrEqual(max);
    expect((value - min) % step).toBe(0);
  });
});
