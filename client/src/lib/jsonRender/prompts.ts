/**
 * The generated-UI prompts, written with json-render's catalog.prompt() and
 * our own templates, from the server's catalogue manifests:
 *
 * - chat_genui_prompt.txt: the show_ui tool's description
 *   (server/config/chat_genui_catalog.json);
 * - genui_prompt.txt: the setup screen's catalogue
 *   (server/config/genui_catalog.json).
 *
 * The server reads the files in server/config. `bun run genui:prompts`
 * (client/scripts/genui-prompts.ts) writes them, and __tests__/prompts.test.ts
 * fails while one is stale. The templates keep the wording and format the
 * server checks replies against: json-render's default template teaches its
 * own JSONL patch format and features the server refuses.
 *
 * The app never imports this module.
 */

import { defineCatalog, defineSchema } from '@json-render/core';
import chat from '../../../../server/config/chat_genui_catalog.json';
import hire from '../../../../server/config/genui_catalog.json';

type ChatManifest = typeof chat;
type HireManifest = typeof hire;

interface JsonSchema {
  enum?: readonly unknown[];
  type?: string | readonly string[];
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  required?: readonly string[];
}

/** A manifest's prompt: json-render's catalog.prompt() with our template. */
function promptOf<M extends { components: object; actions: object }>(manifest: M, template: (manifest: M) => string): string {
  const schema = defineSchema((s) => ({ spec: s.any(), catalog: s.object({ components: s.any(), actions: s.any() }) }), {
    promptTemplate: ({ catalog }) => template(catalog as M),
  });
  return defineCatalog(schema, manifest).prompt();
}

// ----- the show_ui tool's description -----

function typeText(schema: JsonSchema): string {
  if (schema.enum) return schema.enum.map((value) => JSON.stringify(value)).join('|');
  const kind = schema.type;
  if (Array.isArray(kind)) return kind.join('|');
  if (kind === 'array') return `[${typeText(schema.items ?? {})}]`;
  if (kind === 'object') return objectText(schema);
  return String(kind || 'any');
}

function objectText(schema: JsonSchema): string {
  const required = new Set(schema.required ?? []);
  const parts = Object.entries(schema.properties ?? {}).map(([name, prop]) => `${name}${required.has(name) ? '' : '?'}: ${typeText(prop)}`);
  return `{${parts.join(', ')}}`;
}

function chatText(m: ChatManifest): string {
  const limits = m.limits;
  const components = Object.entries(m.components).map(([name, component]) => {
    const children = 'children' in component ? component.children : undefined;
    const held =
      children === 'any' ? '; holds any components' : Array.isArray(children) && children.length ? `; holds ${children.join(', ')}` : '';
    const bind = 'bind' in component && component.bind ? `; binds ${component.bind}` : '';
    return `- ${name} (${component.role}${held}${bind}): ${component.description} Props: ${objectText(component.props)}.`;
  });
  const actions = Object.entries(m.actions).map(([name, action]) => `- ${name} ${objectText(action.params ?? {})}: ${action.description}`);
  return [
    'Show the owner a small interface inside your reply: a choice to make, a short form, numbers, a chart, ' +
      'buttons. Use it when that says it better than words; otherwise just answer. Write your reply as usual: ' +
      'the interface shows under it.',
    '',
    'The spec is json-render\'s flat shape: {"root": "<id>", "state": {...}, "elements": {"<id>": ' +
      '{"type": "<Component>", "props": {...}, "children": ["<id>", ...], "visible": <condition>, ' +
      '"on": {"press": {"action": "<name>", "params": {...}}}}}}. Element ids are letters, digits and _.:-.',
    '',
    'Components:',
    ...components,
    '',
    'State: an input reads and writes its bound prop with {"$bindState": "/path"}; any prop may read the ' +
      'state with {"$state": "/path"} or {"$template": "Hi ${/name}"}. Put each bound value in "state" ' +
      'first. visible: {"$state": "/path"} (truthy), with "eq"/"neq": value, or "not": true; a list ' +
      'means all of them.',
    '',
    'Actions (on.press of a Button; params may read the state with {"$state": "/path"}, read at the press):',
    ...actions,
    `- ${m.custom_actions}`,
    '',
    `Limits: at most ${limits.max_elements} elements, nested at most ${limits.max_depth} deep, ` +
      `${limits.max_children} children each, ${limits.max_bytes} bytes. A spec that breaks a rule is refused ` +
      'with every reason; write it again.',
  ].join('\n');
}

// ----- the setup screen's catalogue -----

const quoted = (values: readonly string[]) => values.map((value) => `"${value}"`).join('|');

/** What a hire's "trigger" is, and what each kind means. */
function triggerRule(m: HireManifest): string {
  return (
    `"trigger" is {"kind": ${quoted(m.trigger.kinds)}, "app"?, "every"?: ${quoted(m.trigger.every)}, ` +
    `"at"?: ${quoted(m.trigger.times)}, "day"?}: ` +
    '"app_event" when a new message or email in an app starts the work, "schedule" for routine work at set ' +
    'times, "manual" when the owner gives them work by messaging them. "at" is the owner\'s local time; "day" ' +
    'is a weekday name for "week", or a day of the month from 1 to 28 for "month".'
  );
}

function hireText(m: HireManifest): string {
  const paths = m.state_paths;
  const components = Object.entries(m.components)
    .filter(([, component]) => !('inserted' in component && component.inserted))
    .map(([name, component]) => `- ${name} ${component.props} — ${component.description}`);
  const actions = Object.entries(m.actions).map(([name, description]) => `- ${name} ${description}`);
  return [
    'Reply ONLY with a JSON object, no code fences: {"text": string, "spec": UISpec}.',
    '"text": one short, warm sentence introducing the new employee by name.',
    '"spec" is the new employee\'s setup screen. Root is a vertical Stack with, in order:',
    '1) AgentCard — a friendly first name, a plain job title as role, one-sentence description, apps they ' +
      'use, status "ready".',
    '2) Plan titled "Their routine" with 3-5 steps; the first step has role "trigger" (e.g. "When a message ' +
      'arrives", "Every weekday at 08:00"). Step titles are short present-tense actions. A step that uses an app ' +
      'names it in "app".',
    `3) Card titled "Ground rules" containing 2-3 Toggles bound to state under "${paths.rules}" through ` +
      `"checked" (always include "${m.ask_first_label}" bound to "${paths.askFirst}", true in "state") and optionally ` +
      `one Choice bound under "${paths.choices}" (e.g. how often to report).`,
    '4) If a needed app is not connected: a Card with tone "trigger" saying so, with a Button whose on.press ' +
      'runs connect_app.',
    '5) A horizontal Stack with Button "Hire {name}" (primary, on.press {"action": "hire_employee", "params": ' +
      '{name, role, apps, trigger, sendsVia}}) and Button "Change something" (secondary, on.press {"action": ' +
      '"refine"}).',
    `${triggerRule(m)} "sendsVia" is the app they answer or report through.`,
    'UISpec is flat: {"root": id, "state": {initial values}, "elements": {id: {"type", "props", "children": ' +
      '[ids], "visible"?: condition, "on"?: {"press": {"action", "params"?}}}}}. Max 12 elements. Every child id ' +
      'must exist.',
    'Each element looks like {"type":"Text","props":{"text":"…"},"children":[]} — all component props go ' +
      'INSIDE "props". A Button\'s action goes in "on", beside "props": ' +
      '{"type":"Button","props":{"label":"…"},"on":{"press":{"action":"refine"}},"children":[]}.',
    'Every Stack and Card MUST list its child ids in "children" — elements not listed as someone\'s child are ' +
      'not shown.',
    'Keep it compact: single-line minified JSON, no code fences, short ids ("a","b","c"…), every string under ' +
      '70 characters, descriptions one short line, step details under 8 words.',
    'Components (use ONLY these):',
    ...components,
    `Tones: ${m.tones.join(', ')}.`,
    'Dynamic props: {"$state":"/path"}, {"$template":"Reports ${/freq}"}, ' +
      '{"$cond":{"$state":"/x","eq":"y"},"$then":a,"$else":b}. Conditions: {"$state":"/path"} with optional ' +
      '"eq" or "not":true.',
    'Actions (the "action" of a Button\'s on.press, with these "params"):',
    ...actions,
    'When asked to change the setup, return the full updated JSON in the same format.',
  ].join('\n');
}

/** Each prompt file the server reads (server/config/<name>), with its text. */
export function genuiPrompts(): Record<string, string> {
  return {
    'chat_genui_prompt.txt': promptOf(chat, chatText),
    'genui_prompt.txt': promptOf(hire, hireText),
  };
}
