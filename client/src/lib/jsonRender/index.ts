/**
 * The glue between json-render (@json-render/core, @json-render/react) and
 * the app, shared by every generated UI: Home's hire setup screen
 * (features/home/genui) and the chat's generated replies.
 *
 * - paths.ts: the state-path rule (no prototype keys) and safe reads/writes
 * - sanitize.ts: specs and streamed patches made safe for json-render
 * - guard.tsx: props read through the component's schema, parent rules,
 *   the one-time live entrance
 * - reveal.ts: the patch stream for a spec, and its paced reveal
 * - uiState.ts: a StateStore that refuses forbidden paths and reports changes
 * - SpecInspector.tsx: the development view of a spec and its patches
 *
 * None of these imports json-render at run time; only a renderer does
 * (registry and Renderer), and that one is loaded lazily, so json-render
 * stays out of the chunks a page loads first. Modules a page loads eagerly
 * import the file they need (paths, sanitize) rather than this index.
 */

export {
  FORBIDDEN_SEGMENTS,
  MAX_PATH_SEGMENTS,
  bindingPath,
  canonicalStatePath,
  escapePointer,
  isForbiddenSegment,
  joinStatePath,
  parseStatePath,
  readPath,
  unescapePointer,
  writePath,
} from './paths';
export {
  DEFAULT_SANITIZE_LIMITS,
  MAX_PATCH_PATH,
  isUsableId,
  isUsableName,
  sanitizeActionBinding,
  sanitizeChildren,
  sanitizeCondition,
  sanitizeElement,
  sanitizeExpressions,
  sanitizeJson,
  sanitizeOn,
  sanitizePatch,
  sanitizeSpec,
  sanitizeState,
  type SanitizeLimits,
  type SpecPatch,
} from './sanitize';
export { LiveUiContext, guarded, parseProps, useEntrance, type GuardOptions, type GuardedComponentProps } from './guard';
export { REVEAL_STEP_MS, specFromPatches, specToPatches, useSpecReveal, type SpecReveal } from './reveal';
export { createUiStateStore, type UiStateChange } from './uiState';
export { SpecInspector } from './SpecInspector';
