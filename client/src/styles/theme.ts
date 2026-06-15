// Theme constants — exported for the canvas + maps surface and the
// useAppTheme overlay map.
//
// SCOPE (post-Wave-14):
//   - `lightColors` / `darkColors` are the two BASE PACKS. The 10-way
//     `THEME_OVERRIDES` map in `client/src/hooks/useAppTheme.ts` merges
//     a per-theme overlay (primary, focus, action palette, edge stroke
//     palette) on top of whichever base pack matches the active theme's
//     dark / light family — see `theme_system.md` for the contract.
//   - `dracula` / `solarized` raw palette constants stay for cases a
//     Tailwind class can't express: SVG `fill=`, React Flow JS edge
//     styles, prismjs token CSS strings, MapSelector / GoogleMaps SDK
//     JS-side colors.
//   - `spacing` / `fontSize` / `fontWeight` / `nodeSize` / `iconSize` /
//     `buttonSize` / `layout` / `transitions` / `constants` stay too —
//     these are not color tokens; they back the canvas-animation engine
//     and a few non-color UI primitives.
//
// AUTHORITATIVE SOURCE for color tokens: the `:root[data-theme="..."]`
// blocks in `client/src/themes/<name>.css` (10 themes) plus the shadcn
// HSL-triplet bridge in `client/src/index.css`. New components consume
// those via Tailwind utilities (`bg-bg-app`, `text-fg-default`,
// `bg-action-run-soft`, `bg-node-agent-soft`) — never `theme.colors.X`
// directly except inside `useAppTheme` consumers (canvas + maps).
//
// Migration cheatsheet for any remaining sites that read this file:
//   theme.dracula.green                  -> className="text-dracula-green"
//   { backgroundColor: theme.dracula.X } -> className="bg-dracula-X"
//   { color: theme.colors.text }         -> className="text-foreground" or "text-fg-default"
//   { backgroundColor: theme.colors.X }  -> className="bg-{token}" or "bg-bg-app"

// ============================================================================
// DRACULA COLOR PALETTE (vibrant action colors + dark theme backgrounds)
// ============================================================================
export const dracula = {
  // Vibrant action colors
  green: '#50fa7b',    // Run/Success - bright green
  purple: '#bd93f9',   // Deploy/Save - purple
  pink: '#ff79c6',     // Cancel/Stop - pink
  cyan: '#8be9fd',     // Info/Alternative - cyan
  red: '#ff5555',      // Error/Danger - red
  orange: '#ffb86c',   // Warning - orange
  yellow: '#f1fa8c',   // Highlight - yellow
  // Dark theme backgrounds
  background: '#282a36',     // Main background
  currentLine: '#44475a',    // Current line / elevated background
  selection: '#44475a',      // Selection / panel background
  comment: '#6272a4',        // Comments / muted text
  foreground: '#f8f8f2',     // Main text
} as const;

// ============================================================================
// SOLARIZED COLOR PALETTE
// ============================================================================
export const solarized = {
  // Base colors (dark to light)
  base03: '#002b36', // darkest background
  base02: '#073642', // dark background highlights
  base01: '#586e75', // dark content tone (comments)
  base00: '#657b83', // light content tone
  base0: '#839496',  // dark content tone
  base1: '#93a1a1',  // light content tone (emphasis)
  base2: '#eee8d5',  // light background highlights
  base3: '#fdf6e3',  // lightest background
  // Accent colors
  yellow: '#b58900',
  orange: '#cb4b16',
  red: '#dc322f',
  magenta: '#d33682',
  violet: '#6c71c4',
  blue: '#268bd2',
  cyan: '#2aa198',
  green: '#859900',
} as const;

// ============================================================================
// LIGHT THEME COLORS - Modern, clean design with proper contrast
// ============================================================================
export const lightColors = {
  // Backgrounds - slightly warmer canvas for depth
  background: '#f5f7fa',          // Slightly blue-gray canvas
  backgroundAlt: '#eef1f5',       // Subtle gray for contrast areas
  backgroundPanel: '#ffffff',     // Pure white for panels
  backgroundElevated: '#ffffff',  // Elevated surfaces
  backgroundHover: 'rgba(0,0,0,0.05)',
  backgroundActive: 'rgba(0,0,0,0.08)',
  backgroundCanvas: '#e8ecf1',    // Cooler gray for canvas - more contrast with white nodes
  // Text - high contrast
  text: '#1a1d21',                // Near-black for readability
  textSecondary: '#374151',       // Darker secondary text (gray-700)
  textMuted: '#4b5563',           // gray-600 - muted but readable
  // Borders - subtle but defined
  border: '#d1d5db',              // gray-300 - more visible
  borderHover: '#9ca3af',         // gray-400
  borderFocus: '#3b82f6',         // Brighter blue for focus
  borderNode: '#c7ccd4',          // Slightly darker for nodes
  // Shadows - more depth for light mode
  shadow: 'rgba(0,0,0,0.12)',
  shadowLight: 'rgba(0,0,0,0.06)',
  shadowHeavy: 'rgba(0,0,0,0.18)',
  shadowNode: '0 2px 8px rgba(0,0,0,0.1), 0 4px 16px rgba(0,0,0,0.06)',
  // Focus
  focus: '#3b82f6',
  focusRing: 'rgba(59, 130, 246, 0.25)',
  // Semantic
  primary: '#2563eb',             // Blue-600 - slightly darker for contrast
  success: '#059669',             // Emerald-600
  warning: '#d97706',             // Amber-600
  error: '#dc2626',               // Red-600
  info: '#0891b2',                // Cyan-600
  // Special
  templateVariable: '#7c3aed',    // Violet for variables
  // Node-specific
  nodeBackground: '#ffffff',
  nodeBorder: '#d1d5db',
  nodeHeaderBg: '#f3f4f6',
  // Action colors (optimized for light backgrounds - darker for contrast)
  actionRun: '#059669',           // Emerald-600
  actionDeploy: '#7c3aed',        // Violet
  actionStop: '#dc2626',          // Red-600
  actionSave: '#0284c7',          // Sky-600
  actionSettings: '#d97706',      // Amber-600
  actionCredentials: '#ca8a04',   // Yellow-600
  actionTheme: '#7c3aed',         // Violet
  actionSidebar: '#0891b2',       // Cyan-600
  actionPalette: '#7c3aed',       // Violet
  statusSaved: '#059669',         // Emerald-600
  statusModified: '#d97706',      // Amber-600
  // Edge colors for light mode - MUCH darker for visibility
  edgeDefault: '#6b7280',         // gray-500 - much more visible
  edgeSelected: '#7c3aed',        // Violet
  edgeExecuting: '#2563eb',       // Blue-600 (executing state pop)
  edgeCompleted: '#16a34a',       // Green-600
  edgeError: '#dc2626',           // Red-600
  edgePending: '#6b7280',         // gray-500 (same as default, dashed)
  edgeMemoryActive: '#db2777',    // Pink-600
  edgeToolActive: '#ea580c',      // Orange-600
  // Category colors for light mode (darker, more saturated)
  categoryWorkflow: '#ea580c',    // Orange-600
  categoryTrigger: '#db2777',     // Pink-600
  categoryAI: '#7c3aed',          // Violet-600
  categoryLocation: '#dc2626',    // Red-600
  categoryWhatsapp: '#059669',    // Emerald-600
  categoryAndroid: '#0891b2',     // Cyan-600
  categoryChat: '#ca8a04',        // Yellow-600
  categoryCode: '#ea580c',        // Orange-600
  categoryUtil: '#7c3aed',        // Violet-600
} as const;

// ============================================================================
// DARK THEME COLORS (Dracula text for better contrast)
// ============================================================================
export const darkColors = {
  // Backgrounds - Solarized dark
  background: solarized.base03,
  backgroundAlt: solarized.base02,
  backgroundPanel: solarized.base02,
  backgroundElevated: '#0d1f2d',
  backgroundHover: 'rgba(255,255,255,0.04)',
  backgroundActive: 'rgba(255,255,255,0.08)',
  backgroundCanvas: solarized.base03,
  // Text - Dracula for better contrast
  text: dracula.foreground,                  // #f8f8f2 - bright white text
  textSecondary: '#bfbfbf',                  // Lighter secondary text
  textMuted: dracula.comment,                // #6272a4 - muted/comments
  // Borders
  border: solarized.base01 + '60',
  borderHover: solarized.base01,
  borderFocus: solarized.blue,
  borderNode: solarized.base01,
  // Shadows
  shadow: 'rgba(0,0,0,0.4)',
  shadowLight: 'rgba(0,0,0,0.25)',
  shadowHeavy: 'rgba(0,0,0,0.5)',
  shadowNode: '0 2px 12px rgba(0,0,0,0.3)',
  // Focus
  focus: solarized.blue,
  focusRing: 'rgba(38, 139, 210, 0.3)',
  // Semantic
  primary: solarized.blue,
  success: solarized.green,
  warning: solarized.yellow,
  error: solarized.red,
  info: solarized.cyan,
  // Special
  templateVariable: solarized.cyan,
  // Node-specific
  nodeBackground: solarized.base02,
  nodeBorder: solarized.base01,
  nodeHeaderBg: solarized.base03,
  // Action colors (Dracula - optimized for dark backgrounds)
  actionRun: dracula.green,        // #50fa7b - bright green
  actionDeploy: dracula.purple,    // #bd93f9 - purple
  actionStop: dracula.pink,        // #ff79c6 - pink
  actionSave: dracula.cyan,        // #8be9fd - cyan
  actionSettings: dracula.orange,  // #ffb86c - orange
  actionCredentials: dracula.yellow, // #f1fa8c - yellow
  actionTheme: dracula.yellow,     // #f1fa8c - yellow (sun icon)
  actionSidebar: dracula.cyan,     // #8be9fd - cyan
  actionPalette: dracula.purple,   // #bd93f9 - purple
  statusSaved: dracula.green,      // #50fa7b - green
  statusModified: dracula.orange,  // #ffb86c - orange
  // Edge colors for dark mode
  edgeDefault: dracula.cyan,       // Cyan
  edgeSelected: dracula.purple,    // Purple
  edgeExecuting: dracula.purple,   // Purple (executing state pop)
  edgeCompleted: dracula.green,    // Green
  edgeError: dracula.red,          // Red
  edgePending: dracula.cyan,       // Cyan (same as default, dashed)
  edgeMemoryActive: dracula.pink,  // Pink
  edgeToolActive: dracula.orange,  // Orange
  // Category colors for dark mode (Dracula - vibrant)
  categoryWorkflow: dracula.orange,
  categoryTrigger: dracula.pink,
  categoryAI: dracula.purple,
  categoryLocation: dracula.red,
  categoryWhatsapp: dracula.green,
  categoryAndroid: dracula.cyan,
  categoryChat: dracula.yellow,
  categoryCode: dracula.orange,
  categoryUtil: dracula.purple,
} as const;

// ============================================================================
// BASE THEME (uses light colors by default)
// ============================================================================
export const theme = {
  // Color palette - use lightColors as default
  colors: lightColors,

  // Solarized accent colors (available in both themes)
  accent: solarized,

  // Dracula vibrant colors (for action buttons)
  dracula: dracula,
  
  // Essential spacing scale
  spacing: {
    xs: '4px',
    sm: '8px',
    md: '12px',
    lg: '16px',
    xl: '20px',
    xxl: '32px',
  },
  
  // Simplified typography
  fontSize: {
    xs: '11px',
    sm: '12px',
    base: '14px',
    lg: '16px',
    xl: '18px',
  },
  
  fontWeight: {
    normal: '400',
    medium: '500',
    semibold: '600',
  },

  fontFamily: {
    sans: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    mono: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, monospace',
  },

  borderRadius: {
    sm: '4px',
    md: '6px',
    lg: '8px',
  },

  // Node sizes (fixed per industry standard)
  nodeSize: {
    square: '64px',          // SquareNode (design-system spec: 64px box)
    squareIcon: '28px',      // Icon inside square node (≈ 0.44 × box)
    toolkitWidth: '120px',   // ToolkitNode width (rectangular, wider)
    toolkitHeight: '50px',   // ToolkitNode height (shorter)
    handle: '8px',           // Connection handles
    statusIndicator: '10px', // Status dot
    paramButton: '16px',     // Parameters gear button
    outputBadge: '14px',     // Output data indicator
  },

  // Icon sizes
  iconSize: {
    xs: '12px',
    sm: '16px',
    md: '24px',
    lg: '28px',
    xl: '32px',
  },

  // Button sizes
  buttonSize: {
    sm: '24px',
    md: '32px',
    lg: '34px',
  },

  // Layout constants
  layout: {
    sidebarWidth: '288px',
    workflowSidebarWidth: '280px',
    parameterPanelWidth: '320px',
    headerHeight: '60px',
    toolbarHeight: '48px',
  },
  
  transitions: {
    fast: '0.2s ease',
    medium: '0.3s ease',
  },
  
  // App constants - moved from utils/constants.ts
  constants: {
    storageKeys: {
      workflows: 'react-flow-workflows',
      workflowData: (id: string) => `react-flow-workflows-${id}`,
    },
    defaultWorkflowName: 'Untitled Workflow',
    debounceDelay: {
      workflowUpdate: 100,
      search: 300,
    },
    gridSize: 20,
    defaultNodePosition: { x: 100, y: 200 },
    dragOffset: { x: 75, y: 50 },
  },
};

// Component styles - consolidated from components.ts + theme.ts
export const styles = {
  // Button variants
  button: {
    base: {
      padding: `${theme.spacing.sm} ${theme.spacing.lg}`,
      borderRadius: theme.borderRadius.md,
      fontSize: theme.fontSize.sm,
      fontWeight: theme.fontWeight.medium,
      cursor: 'pointer',
      transition: theme.transitions.fast,
      border: 'none',
      outline: 'none',
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      gap: theme.spacing.sm,
    },
    primary: {
      backgroundColor: theme.colors.focus,
      color: 'white',
    },
    secondary: {
      backgroundColor: 'transparent',
      border: `1px solid ${theme.colors.border}`,
      color: theme.colors.textSecondary,
    },
    danger: {
      backgroundColor: '#ef4444',
      color: 'white',
    },
  },

  // Input styles
  input: {
    base: {
      width: '100%',
      padding: `${theme.spacing.sm} ${theme.spacing.md}`,
      fontSize: theme.fontSize.base,
      border: `1px solid ${theme.colors.border}`,
      borderRadius: theme.borderRadius.md,
      backgroundColor: theme.colors.background,
      color: theme.colors.text,
      fontFamily: 'system-ui, sans-serif',
      outline: 'none',
      transition: `border-color ${theme.transitions.fast}`,
    },
  },

  // Card styles
  card: {
    base: {
      backgroundColor: theme.colors.background,
      border: `1px solid ${theme.colors.border}`,
      borderRadius: theme.borderRadius.lg,
      boxShadow: `0 1px 2px ${theme.colors.shadowLight}`,
      transition: theme.transitions.fast,
    },
  },

  // Modal styles
  modal: {
    overlay: {
      position: 'fixed' as const,
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      backgroundColor: 'rgba(0, 0, 0, 0.5)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000,
      padding: theme.spacing.xl,
    },
    content: {
      backgroundColor: theme.colors.background,
      borderRadius: theme.borderRadius.lg,
      boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.25)',
      width: '100%',
      maxHeight: '90vh',
      display: 'flex',
      flexDirection: 'column' as const,
      overflow: 'hidden',
    },
    header: {
      padding: theme.spacing.xl,
      borderBottom: `1px solid ${theme.colors.border}`,
      backgroundColor: theme.colors.backgroundAlt,
    },
    title: {
      margin: 0,
      fontSize: theme.fontSize.xl,
      fontWeight: theme.fontWeight.semibold,
      color: theme.colors.text,
    },
  },

  // Layout components
  sidebar: {
    width: '100%',
    height: '100%',
    overflowY: 'auto' as const,
    backgroundColor: theme.colors.backgroundPanel,
    display: 'flex',
    flexDirection: 'column' as const,
  },
  
  sidebarHeader: {
    padding: theme.spacing.xl,
    borderBottom: `1px solid ${theme.colors.border}`,
    backgroundColor: theme.colors.background,
  },
  
  sidebarTitle: {
    margin: 0,
    fontSize: theme.fontSize.xl,
    fontWeight: theme.fontWeight.semibold,
    color: theme.colors.text,
    fontFamily: 'system-ui, sans-serif',
  },

  // Component item styles
  componentItem: {
    padding: '10px',
    marginBottom: theme.spacing.sm,
    backgroundColor: theme.colors.background,
    border: `1px solid ${theme.colors.border}`,
    borderRadius: theme.borderRadius.md,
    cursor: 'grab',
    transition: theme.transitions.fast,
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    boxShadow: `0 1px 2px ${theme.colors.shadowLight}`,
    fontFamily: 'system-ui, sans-serif',
  },

  componentIcon: {
    fontSize: theme.fontSize.lg,
    width: '28px',
    height: '28px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: theme.borderRadius.md,
    color: theme.colors.background,
    boxShadow: `0 2px 4px ${theme.colors.shadow}`,
    flexShrink: 0,
  },

  componentTitle: {
    fontWeight: theme.fontWeight.medium,
    fontSize: theme.fontSize.base,
    color: theme.colors.text,
    marginBottom: '1px',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },

  componentDescription: {
    fontSize: theme.fontSize.sm,
    color: theme.colors.textSecondary,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },

  // Section styles
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: `${theme.spacing.sm} ${theme.spacing.md}`,
    fontSize: theme.fontSize.sm,
    fontWeight: theme.fontWeight.semibold,
    color: theme.colors.textSecondary,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
    fontFamily: 'system-ui, sans-serif',
    backgroundColor: theme.colors.backgroundPanel,
    border: `1px solid ${theme.colors.border}`,
    borderRadius: theme.borderRadius.md,
    cursor: 'pointer',
    transition: theme.transitions.fast,
    userSelect: 'none' as const,
  },

  sectionContent: {
    overflow: 'hidden',
    transition: `max-height ${theme.transitions.medium}, opacity ${theme.transitions.fast}`,
  },

  // Layout containers
  mainContainer: {
    width: '100%',
    height: '100%',
    display: 'flex',
    backgroundColor: theme.colors.background,
    color: theme.colors.text,
    fontFamily: 'system-ui, sans-serif',
  },

  canvasContainer: {
    flex: 1,
    height: '100%',
    position: 'relative' as const,
    backgroundColor: theme.colors.backgroundAlt,
    transition: `margin-left ${theme.transitions.medium}`,
  },

  reactFlowContainer: {
    width: '100%',
    height: '100%',
    backgroundColor: theme.colors.background,
  },

  // Code block / syntax highlighted JSON (prismjs) — used by
  // NodeOutputPanel, OutputDisplayPanel, and any future JSON display.
  // Token colors use dracula palette for consistency with CodeEditor.
  codeBlock: {
    container: {
      margin: 0,
      fontFamily: theme.fontFamily.mono,
      fontSize: theme.fontSize.sm,
      lineHeight: 1.6,
      overflow: 'auto' as const,
      whiteSpace: 'pre-wrap' as const,
      wordBreak: 'break-word' as const,
    } as React.CSSProperties,
    maxHeight: '400px',
  },
};

/**
 * Generate prismjs token CSS for a given theme instance.
 * Call via `getPrismTokenCSS(theme)` and inject once per component
 * tree that uses `Prism.highlight`. Same dracula token mapping as
 * CodeEditor.tsx — centralised here so every code display in the
 * app shares one source of truth.
 *
 * @param selector  CSS parent selector to scope the tokens (e.g.
 *                  '.prism-json' or '.code-editor-container').
 *                  Defaults to '.prism-code' which all prism output
 *                  containers should use as a className.
 */
export function getPrismTokenCSS(
  t: { dracula: typeof dracula; colors: { text?: string }; [k: string]: any },
  selector: string = '.prism-code',
): string {
  return `
    ${selector} .token.property { color: ${t.dracula.cyan}; }
    ${selector} .token.string { color: ${t.dracula.yellow}; }
    ${selector} .token.number { color: ${t.dracula.purple}; }
    ${selector} .token.boolean { color: ${t.dracula.purple}; }
    ${selector} .token.null { color: ${t.dracula.orange}; }
    ${selector} .token.keyword { color: ${t.dracula.pink}; }
    ${selector} .token.function { color: ${t.dracula.green}; }
    ${selector} .token.operator { color: ${t.dracula.pink}; }
    ${selector} .token.class-name { color: ${t.dracula.cyan}; }
    ${selector} .token.builtin { color: ${t.dracula.cyan}; }
    ${selector} .token.comment { color: ${t.dracula.comment}; }
    ${selector} .token.punctuation { color: ${t.dracula.foreground}; }
  `;
}

