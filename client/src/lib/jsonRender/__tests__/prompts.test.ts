/**
 * The prompt files the server reads (server/config/*_prompt.txt) are the
 * ones json-render's catalog.prompt() writes from today's manifests and
 * templates (prompts.ts). When this fails, run `bun run genui:prompts` and
 * commit the files.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { genuiPrompts } from '../prompts';

describe('the generated-UI prompts', () => {
  it.each(Object.entries(genuiPrompts()))('server/config/%s is current (bun run genui:prompts)', (name, text) => {
    expect(readFileSync(resolve(__dirname, '../../../../../server/config', name), 'utf-8')).toBe(text);
  });
});
