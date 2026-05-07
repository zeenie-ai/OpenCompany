/**
 * Credential system types.
 * Every provider is described by a single ProviderConfig object.
 * ALL conditional logic lives in config — panel components are pure renderers.
 */

import type { ActionButtonIntent } from '@/components/ui/action-button';
export type { ActionButtonIntent };

// ============================================================================
// Panel kinds — one renderer branch per kind
// ============================================================================

export type PanelKind = 'apiKey' | 'oauth' | 'qrPairing' | 'email';

// ============================================================================
// Field schema — drives antd Form.Item rendering via FieldRenderer
// ============================================================================

export interface FieldDef {
  key: string;
  label: string;
  secret?: boolean;
  placeholder?: string;
  /** Pre-fill value when nothing stored. See ServerFieldDef.default. */
  default?: string;
  required?: boolean;
}

// ============================================================================
// Status row — drives StatusCard rendering from data
// ============================================================================

export interface StatusRowDef {
  label: string;
  /** Extract boolean from status object. StatusCard handles themed Tag rendering. */
  ok: (status: any) => boolean;
  trueText: string;
  falseText: string;
  /** Use warning color instead of error when not ok. */
  warn?: boolean;
}

// ============================================================================
// Action — drives ActionBar rendering from data
// ============================================================================

export interface ActionDef {
  key: string;
  label: string;
  /** Semantic role consumed by `<ActionButton intent="...">`. Themes
   * remap the underlying --action-X tokens without touching call sites. */
  intent: ActionButtonIntent;
  /** Return true to hide this action. */
  hidden?: (status: any, stored: boolean) => boolean;
  /** Return true to disable this action. */
  disabled?: (status: any, stored: boolean) => boolean;
}

// ============================================================================
// QR pairing config — everything WhatsApp/Android-specific lives here
// ============================================================================

export interface QrPairingDef {
  /** Path to QR data in status object. */
  qrField: string;
  /** Check if device is connected/paired. */
  isConnected: (status: any) => boolean;
  connectedTitle: string;
  connectedSubtitle: (status: any) => string;
  /** Loading state for QR display. */
  isLoading: (status: any) => boolean;
  /** Empty text when no QR available. */
  emptyText: (status: any, stored: boolean) => string;
  scanText: string;
}

// ============================================================================
// Provider config — the ONLY thing you add to register a new provider
// ============================================================================

export interface ProviderConfig {
  id: string;
  name: string;
  category: string;
  categoryLabel: string;
  /** Theme color key (resolved via theme.colors[color] at render). */
  color: string;
  kind: PanelKind;
  /** Provider icon ref string (e.g. `lobehub:Claude`, `lucide:Mail`,
   *  `asset:gmail`, emoji). Resolved at render time by `<NodeIcon>` so
   *  the same dispatch path (lib component / image / text) is used for
   *  every icon in the app. Sizing flows from the wrapper's Tailwind
   *  classes (`h-6 w-6`) — no pixel literals at call sites. */
  iconRef: string;

  /** Credential input fields. */
  fields?: FieldDef[];
  /** WebSocket commands for OAuth flows. */
  ws?: { login: string; logout: string; status: string };
  /** Which status hook to read. */
  statusHook?: 'whatsapp' | 'android' | 'twitter' | 'google' | 'telegram';
  /** Config-driven status rows (replaces per-provider conditionals). */
  statusRows?: StatusRowDef[];
  /** Config-driven actions (replaces per-provider handler code). */
  actions?: ActionDef[];
  /** QR pairing config (replaces isWhatsApp/isAndroid conditionals). */
  qr?: QrPairingDef;

  /** Non-standard validation type ('google_maps' | 'apify'). */
  validateAs?: string;
  /** OAuth callback URL shown as help text. */
  callbackUrl?: string;
  /** Help text shown under OAuth credential fields. */
  instructions?: string;
  /** Show AI provider defaults section (model, temperature, thinking). */
  hasDefaults?: boolean;
  /** Show WhatsApp rate limit configuration section. */
  hasRateLimits?: boolean;
  /** Service key for API cost tracking section (twitter, google_workspace, google_maps). */
  usageService?: string;
  /** Server-resolved: whether a key/token exists in the credentials DB. */
  stored?: boolean;
  /** Connected account identifier (email or display name) for OAuth providers. */
  account_label?: string | null;
}

// ============================================================================
// Category — derived from providers, not declared separately
// ============================================================================

export interface CategoryGroup {
  key: string;
  label: string;
  items: ProviderConfig[];
}
