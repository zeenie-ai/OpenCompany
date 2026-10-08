/**
 * The setup-screen pipeline, as pages see it. Everything else in this
 * folder (the catalogue, parser, normalizer, json-render registry and
 * store) is private to it; eslint.config.js refuses imports of those from
 * outside. The screen's renderer loads lazily (HireDraftPanel), so nothing
 * exported here brings json-render with it.
 */

export { HIRE_LIMITS } from './hirePayload';
export { HireDraftPanel } from './HireDraftPanel';
export { MessagePreview as DraftMessagePreview } from './MessagePreview';
export { useHireComposer, useJobComposer, useSendJob } from './useHireComposer';
export { useStarterHire } from './useStarterHire';
