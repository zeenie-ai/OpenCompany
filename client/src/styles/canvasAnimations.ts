/**
 * Canvas-wide animation + status styles injected once into <style> by
 * Dashboard. Split into named groups so a new status visual or keyframe
 * can be added without touching Dashboard.tsx.
 *
 *   KEYFRAMES                -- @keyframes definitions for edges
 *   edgeStatusStyles(...)    -- .react-flow__edge.{selected,executing,...}
 *   nodeStatusStyles(...)    -- .react-flow__node.{...} (status-class colors only)
 *   buildCanvasStyles(...)   -- composes the three for Dashboard
 *
 * Per-node inline animations (border pulse, etc.) live in their own
 * components and read theme tokens directly; this module is for
 * canvas-wide rules that need to match React Flow's wrapper classes.
 *
 * Node execution glow is owned by `client/src/themes/base.css` — see
 * the `node-pulse` keyframe + `.react-flow__node.executing .node` /
 * `.sq-node[data-executing] .sq-node-box` rules there. This file used
 * to inject a competing `nodeGlow` keyframe targeting the React Flow
 * wrapper; that was dead code (only the inner `.node` child animated)
 * and has been removed in favour of base.css as the single source of
 * truth.
 *
 * The light vs dark distinction is encoded entirely in `colors` (the
 * theme object provides different values per mode), so this file knows
 * nothing about which theme is active.
 */

export interface CanvasStatusColors {
  edgeDefault: string;
  edgeSelected: string;
  edgeExecuting: string;
  edgeCompleted: string;
  edgeError: string;
  edgePending: string;
  edgeMemoryActive: string;
  edgeToolActive: string;
}

const KEYFRAMES = `
  @keyframes dashFlow {
    0% { stroke-dashoffset: 24; }
    100% { stroke-dashoffset: 0; }
  }
`;

function edgeStatusStyles(colors: CanvasStatusColors): string {
  return `
  .react-flow__edge path {
    stroke: ${colors.edgeDefault} !important;
    stroke-width: 2px;
  }

  .react-flow__edge.selected path {
    stroke: ${colors.edgeSelected} !important;
    stroke-width: 4px !important;
  }

  .react-flow__edge.executing path {
    stroke: ${colors.edgeExecuting} !important;
    stroke-width: 3px !important;
    stroke-dasharray: 8 4;
    animation: dashFlow 0.5s linear infinite;
  }

  .react-flow__edge.completed path {
    stroke: ${colors.edgeCompleted} !important;
    stroke-width: 2px !important;
  }

  .react-flow__edge.error path {
    stroke: ${colors.edgeError} !important;
    stroke-width: 3px !important;
  }

  .react-flow__edge.pending path {
    stroke: ${colors.edgePending} !important;
    stroke-width: 2px !important;
    stroke-dasharray: 8 4;
    animation: dashFlow 0.5s linear infinite;
  }

  .react-flow__edge.memory-active path {
    stroke: ${colors.edgeMemoryActive} !important;
    stroke-width: 3px !important;
  }

  .react-flow__edge.tool-active path {
    stroke: ${colors.edgeToolActive} !important;
    stroke-width: 3px !important;
  }
`;
}

function nodeStatusStyles(_colors: CanvasStatusColors): string {
  // Node execution animation is owned by base.css (`node-pulse`
  // keyframe + `.react-flow__node.executing .node` /
  // `.sq-node[data-executing] .sq-node-box` rules). This function is
  // retained as a hook for future canvas-wide status-class rules on
  // the React Flow wrapper that don't fit per-component CSS.
  //
  // `_colors` is intentionally unused at the moment; the parameter is
  // kept on the signature so callers (buildCanvasStyles) and the
  // CanvasStatusColors contract stay stable for downstream consumers.
  return '';
}

export function buildCanvasStyles(colors: CanvasStatusColors): string {
  return [
    edgeStatusStyles(colors),
    nodeStatusStyles(colors),
    KEYFRAMES,
  ].join('\n');
}
