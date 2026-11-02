/**
 * Writes the generated-UI prompt files the server reads
 * (server/config/*_prompt.txt) from the catalogue manifests, through
 * json-render's catalog.prompt() and our templates
 * (src/lib/jsonRender/prompts.ts).
 *
 * Run `bun run genui:prompts` after changing a manifest or a template and
 * commit the files with it; src/lib/jsonRender/__tests__/prompts.test.ts
 * fails while one is stale.
 */

import { writeFileSync } from 'node:fs';
import { genuiPrompts } from '../src/lib/jsonRender/prompts';

for (const [name, text] of Object.entries(genuiPrompts())) {
  writeFileSync(new URL(`../../server/config/${name}`, import.meta.url), text);
  console.log(`wrote server/config/${name}`);
}
