/**
 * The glue between json-render (@json-render/core, @json-render/react) and
 * the app, shared by every generated UI: Home's hire setup screen
 * (features/home/genui) and the chat's generated replies.
 *
 * - paths.ts: the state-path rule (no prototype keys) and safe reads/writes
 * - sanitize.ts: specs made safe for json-render
 * - catalog.ts: the forgiving prop schemas and the catalogue shape
 * - guard.tsx: props read through the component's schema, parent rules,
 *   the one-time live entrance
 * - reveal.ts: the patch stream for a spec, and its paced reveal
 * - prompts.ts: the prompt files the server reads (never loaded by the app)
 * - SpecInspector.tsx: the development view of a spec and its patches
 *
 * None of these but prompts.ts imports json-render at run time; only a
 * renderer does (registry and Renderer), and that one is loaded lazily, so json-render
 * stays out of the chunks a page loads first. Modules a page loads eagerly
 * import the file they need (paths, sanitize, catalog) rather than this
 * index.
 */

export { readPath } from './paths';
export { LiveUiContext, guarded, type GuardedComponentProps } from './guard';
export { REVEAL_STEP_MS, specToPatches, useSpecReveal, type SpecReveal } from './reveal';
export { SpecInspector } from './SpecInspector';
