/* eslint-disable react-refresh/only-export-components -- canonical React Context pattern co-locates Provider + hooks/helpers/types in one file. */
/**
 * WebSocket Context for real-time communication with Python backend.
 *
 * Provides WebSocket connection for:
 * - Request/response operations (parameters, execution, API keys)
 * - Real-time broadcasts (status updates, multi-client sync)
 * - Android device connection status
 * - Node execution status (scoped by workflow_id - n8n pattern)
 * - Variable/parameter updates
 * - Workflow state changes
 */

import React, { createContext, useContext, useEffect, useState, useCallback, useRef, useMemo } from 'react';
import ReconnectingWebSocket from 'partysocket/ws';
import { API_CONFIG } from '../config/api';
import { useAppStore } from '../store/useAppStore';
import { useAuth } from './AuthContext';
import { queryClient } from '../lib/queryClient';
import { queryKeys } from '../lib/queryConfig';
import { invalidateCatalogue } from '../hooks/useCatalogueQuery';
import { nodeParamsQueryKey } from '../hooks/useNodeParamsQuery';
import { WORKFLOWS_QUERY_KEY } from '../hooks/useWorkflowsQuery';
import type { WorkflowEvent } from '../types/cloudEvents';
import { WS_CLOSE, WS_RECONNECT } from '../lib/connectionConfig';
import {
  useNodeStatusStore,
  useNodeStatusForId,
  useCurrentWorkflowStatuses,
} from '../stores/nodeStatusStore';

// Generate unique request ID
const generateRequestId = (): string => {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
};

// Pending request tracking
interface PendingRequest {
  resolve: (value: any) => void;
  reject: (reason: any) => void;
  timeout: ReturnType<typeof setTimeout> | null;  // null for no timeout (trigger nodes)
}

// Queued send tracking (for replay after reconnect)
interface QueuedSend {
  type: string;
  data: Record<string, any> | undefined;
  resolve: (value: any) => void;
  reject: (reason: any) => void;
  enqueuedAt: number;
  timeoutMs: number; // -1 = no timeout, otherwise the per-request budget
  abortController: AbortController; // for clean cancellation of queue-side timeout
}

// Request timeout (30 seconds)
const REQUEST_TIMEOUT = 30000;

// Maximum queued sends before backpressure kicks in (FIFO eviction of oldest)
const QUEUE_MAX_SIZE = 200;

// Trigger node types that wait indefinitely for events
const TRIGGER_NODE_TYPES = ['whatsappReceive', 'webhookTrigger', 'cronScheduler', 'chatTrigger', 'telegramReceive'];

// Agent node types that can run for minutes (no timeout)
const LONG_RUNNING_NODE_TYPES = [
  'aiAgent', 'chatAgent', 'rlm_agent',
  'android_agent', 'coding_agent', 'web_agent', 'task_agent', 'social_agent',
  'travel_agent', 'tool_agent', 'productivity_agent', 'payments_agent', 'consumer_agent',
  'autonomous_agent', 'orchestrator_agent', 'ai_employee',
];

// Status types
export interface AndroidStatus {
  connected: boolean;
  paired: boolean;
  device_id: string | null;
  device_name: string | null;
  connected_devices: string[];
  connection_type: string | null;
  qr_data: string | null;
  session_token: string | null;
}

export interface NodeStatus {
  status: 'idle' | 'executing' | 'success' | 'error' | 'waiting';
  data?: Record<string, any>;
  output?: any;
  timestamp?: number;
  // Per-workflow scoping (n8n pattern)
  workflow_id?: string;
  // Waiting state data
  message?: string;
  waiter_id?: string;
  timeout?: number;
}

export interface WorkflowStatus {
  executing: boolean;
  current_node: string | null;
  progress?: number;
}

export interface DeploymentStatus {
  isRunning: boolean;
  activeRuns: number;
  status: 'idle' | 'starting' | 'running' | 'stopped' | 'cancelled' | 'error';
  workflow_id?: string | null;  // Which workflow is deployed (for scoping)
  totalTime?: number;
  error?: string;
}

export interface WorkflowLock {
  locked: boolean;
  workflow_id: string | null;
  locked_at: number | null;
  reason: string | null;
}

export interface WhatsAppStatus {
  connected: boolean;
  has_session: boolean;
  running: boolean;
  pairing: boolean;
  device_id?: string;
  connected_phone?: string;
  qr?: string;
  timestamp?: number;
}

export interface TwitterStatus {
  connected: boolean;
  username: string | null;
  user_id: string | null;
  name?: string;
  profile_image_url?: string;
  verified?: boolean;
}

export interface GoogleStatus {
  connected: boolean;
  email: string | null;
  name?: string;
  profile_image_url?: string;
}

export interface TelegramStatus {
  connected: boolean;
  bot_username: string | null;
  bot_name: string | null;
  bot_id: string | null;
  owner_chat_id: number | null;
}

// WhatsApp Rate Limit types (from Go RPC schema)
export interface RateLimitConfig {
  enabled: boolean;
  min_delay_ms: number;
  max_delay_ms: number;
  typing_delay_ms: number;
  link_extra_delay_ms: number;
  max_messages_per_minute: number;
  max_messages_per_hour: number;
  max_new_contacts_per_day: number;
  simulate_typing: boolean;
  randomize_delays: boolean;
  pause_on_low_response: boolean;
  response_rate_threshold: number;
}

export interface RateLimitStats {
  messages_sent_last_minute: number;
  messages_sent_last_hour: number;
  messages_sent_today: number;
  new_contacts_today: number;
  responses_received: number;
  response_rate: number;
  is_paused: boolean;
  pause_reason?: string;
}

/**
 * In-memory validation-result cache for an API-key provider.
 *
 * NOT a "do we have a stored key" answer — that comes from the
 * `useCatalogueQuery['credentialCatalogue']` `provider.stored` field
 * (single source of truth, derived from the encrypted DB on each
 * `get_credential_catalogue` round-trip). Mixing the two flags caused
 * cross-tab drift; the duplicated `hasKey` field was retired.
 */
export interface ApiKeyStatus {
  valid: boolean;
  message?: string;
  models?: string[];
  timestamp?: number;
}

// Console log entry from Console nodes
export interface ConsoleLogEntry {
  node_id: string;
  label: string;
  timestamp: string;
  data: any;
  formatted: string;
  format: 'json' | 'json_compact' | 'text' | 'table';
  workflow_id?: string;
  // Source node info (the node whose output is being logged)
  source_node_id?: string;
  source_node_type?: string;
  source_node_label?: string;
}

// Terminal/server log entry
export interface TerminalLogEntry {
  timestamp: string;
  level: 'debug' | 'info' | 'warning' | 'error';
  message: string;
  source?: string;  // e.g., 'workflow', 'ai', 'android', 'whatsapp'
  details?: any;
}

// Chat message for chatTrigger nodes
export interface ChatMessage {
  role: 'user' | 'assistant';
  message: string;
  timestamp: string;
  session_id?: string;
}

// WhatsApp received message structure (from Go service via whatsapp_message_received event)
export interface WhatsAppMessage {
  message_id: string;
  sender: string;
  chat_id: string;
  type: 'text' | 'image' | 'video' | 'audio' | 'document' | 'location' | 'contact' | 'sticker';
  text?: string;
  timestamp: number;
  is_group: boolean;
  push_name?: string;
  media_url?: string;
  media_data?: string;  // Base64 if includeMediaData is enabled
  caption?: string;
  // Location message fields
  latitude?: number;
  longitude?: number;
  // Contact message fields
  contact_name?: string;
  vcard?: string;
}

export interface NodeParameters {
  parameters: Record<string, any>;
  version: number;
}

// Per-session compaction/token usage stats (pushed by backend broadcasts)
export interface CompactionStats {
  session_id: string;
  total: number;
  threshold: number;
  context_length?: number;
  count: number;
  total_cost?: number;
}

export interface FullStatus {
  android: AndroidStatus;
  api_keys: Record<string, ApiKeyStatus>;
  nodes: Record<string, NodeStatus>;
  node_parameters: Record<string, NodeParameters>;
  variables: Record<string, any>;
  workflow: WorkflowStatus;
}

// Context value type
interface WebSocketContextValue {
  // Connection state
  isConnected: boolean;
  /**
   * `true` once the socket is open AND the post-open init burst has
   * settled (api-key probes, terminal/chat/console history). Queries
   * that depend on backend-served data should gate on this rather
   * than `isConnected` so they fire once instead of racing the burst.
   */
  isReady: boolean;
  reconnecting: boolean;

  // Status data
  androidStatus: AndroidStatus;
  setAndroidStatus: React.Dispatch<React.SetStateAction<AndroidStatus>>;
  whatsappStatus: WhatsAppStatus;
  twitterStatus: TwitterStatus;
  googleStatus: GoogleStatus;
  telegramStatus: TelegramStatus;
  whatsappMessages: WhatsAppMessage[];  // History of received messages
  lastWhatsAppMessage: WhatsAppMessage | null;  // Most recent message
  apiKeyStatuses: Record<string, ApiKeyStatus>;
  consoleLogs: ConsoleLogEntry[];  // Console node output logs
  terminalLogs: TerminalLogEntry[];  // Server/terminal logs
  chatMessages: ChatMessage[];  // Chat messages for chatTrigger
  nodeStatuses: Record<string, NodeStatus>;  // Current workflow's node statuses
  nodeParameters: Record<string, NodeParameters>;
  variables: Record<string, any>;
  workflowStatus: WorkflowStatus;
  deploymentStatus: DeploymentStatus;
  workflowLock: WorkflowLock;
  compactionStats: Record<string, CompactionStats>;  // session_id -> stats (current workflow)

  // Status getters
  getNodeStatus: (nodeId: string) => NodeStatus | undefined;
  getApiKeyStatus: (provider: string) => ApiKeyStatus | undefined;
  getVariable: (name: string) => any;

  // Compaction stats
  updateCompactionStats: (workflowId: string, sessionId: string, stats: CompactionStats) => void;
  requestStatus: () => void;
  clearNodeStatus: (nodeId: string) => Promise<void>;
  clearWhatsAppMessages: () => void;
  clearConsoleLogs: () => void;
  clearTerminalLogs: () => void;
  clearChatMessages: () => void;
  sendChatMessage: (message: string, nodeId?: string) => Promise<void>;

  // Generic request method
  sendRequest: <T = any>(type: string, data?: Record<string, any>) => Promise<T>;

  // Generic broadcast subscription. Returns an unsubscribe fn.
  // Use for ad-hoc backend-pushed events like `workflow_ops_apply`
  // (see server/services/status_broadcaster.send_custom_event) so a
  // new listener doesn't require a new switch case + state slice.
  addEventListener: (type: string, handler: (data: any) => void) => () => void;

  // Node Parameters
  getNodeParameters: (nodeId: string) => Promise<NodeParameters | null>;
  getAllNodeParameters: (nodeIds: string[]) => Promise<Record<string, NodeParameters>>;
  saveNodeParameters: (nodeId: string, parameters: Record<string, any>, version?: number) => Promise<boolean>;
  deleteNodeParameters: (nodeId: string) => Promise<boolean>;

  // Node Execution
  executeNode: (nodeId: string, nodeType: string, parameters: Record<string, any>, nodes?: any[], edges?: any[]) => Promise<any>;
  executeWorkflow: (nodes: any[], edges: any[], sessionId?: string) => Promise<any>;
  getNodeOutput: (nodeId: string, outputName?: string) => Promise<any>;

  // Trigger/Event Waiting
  cancelEventWait: (nodeId: string, waiterId?: string) => Promise<{ success: boolean; cancelled_count?: number }>;

  // Deployment Operations
  deployWorkflow: (workflowId: string, nodes: any[], edges: any[], sessionId?: string) => Promise<any>;
  cancelDeployment: (workflowId?: string) => Promise<any>;
  getDeploymentStatus: (workflowId?: string) => Promise<{ isRunning: boolean; activeRuns: number; settings?: any; workflow_id?: string }>;
  cancelExecution: (workflowId: string, nodeId?: string) => Promise<any>;
  getWorkflowStatus: (workflowId: string) => Promise<{ executing: boolean }>;

  // AI Operations
  executeAiNode: (nodeId: string, nodeType: string, parameters: Record<string, any>, model: string, workflowId: string, nodes: any[], edges: any[]) => Promise<any>;
  getAiModels: (provider: string, apiKey: string) => Promise<string[]>;

  // API Key Operations
  validateApiKey: (provider: string, apiKey: string) => Promise<{ valid: boolean; message?: string; models?: string[] }>;
  getStoredApiKey: (provider: string) => Promise<{ hasKey: boolean; apiKey?: string; models?: string[] }>;
  saveApiKey: (provider: string, apiKey: string, models?: string[]) => Promise<boolean>;
  deleteApiKey: (provider: string) => Promise<boolean>;

  // Android Operations
  getAndroidDevices: () => Promise<string[]>;
  executeAndroidAction: (serviceId: string, action: string, parameters: Record<string, any>, deviceId?: string) => Promise<any>;

  // Maps Operations
  validateMapsKey: (apiKey: string) => Promise<{ valid: boolean; message?: string }>;

  // Apify Operations
  validateApifyKey: (apiKey: string) => Promise<{ valid: boolean; message?: string; username?: string }>;

  // WhatsApp Operations
  getWhatsAppStatus: () => Promise<{ connected: boolean; deviceId?: string; data?: any }>;
  getWhatsAppQR: () => Promise<{ connected: boolean; qr?: string; message?: string }>;
  sendWhatsAppMessage: (phone: string, message: string) => Promise<{ success: boolean; messageId?: string; error?: string }>;
  startWhatsAppConnection: () => Promise<{ success: boolean; message?: string }>;
  restartWhatsAppConnection: () => Promise<{ success: boolean; message?: string }>;
  getWhatsAppGroups: () => Promise<{ success: boolean; groups: Array<{ jid: string; name: string; topic?: string; size?: number; is_community?: boolean }>; error?: string }>;
  getWhatsAppChannels: () => Promise<{ success: boolean; channels: Array<{ jid: string; name: string; subscriber_count?: number; role?: string }>; error?: string }>;
  getWhatsAppGroupInfo: (groupId: string) => Promise<{ success: boolean; participants: Array<{ phone: string; name: string; jid: string; is_admin?: boolean }>; name?: string; error?: string }>;
  getWhatsAppRateLimitConfig: () => Promise<{ success: boolean; config?: RateLimitConfig; stats?: RateLimitStats; error?: string }>;
  setWhatsAppRateLimitConfig: (config: Partial<RateLimitConfig>) => Promise<{ success: boolean; config?: RateLimitConfig; error?: string }>;
  getWhatsAppRateLimitStats: () => Promise<{ success: boolean; stats?: RateLimitStats; error?: string }>;
  unpauseWhatsAppRateLimit: () => Promise<{ success: boolean; stats?: RateLimitStats; error?: string }>;

  // Memory and Skill Operations
  clearMemory: (sessionId: string, clearLongTerm?: boolean, workflowId?: string) => Promise<{ success: boolean; default_content?: string; cleared_vector_store?: boolean; cleared_todo_keys?: string[]; error?: string }>;
  resetSkill: (skillName: string) => Promise<{ success: boolean; original_content?: string; is_builtin?: boolean; error?: string }>;
}

// Default values
const defaultAndroidStatus: AndroidStatus = {
  connected: false,
  paired: false,
  device_id: null,
  device_name: null,
  connected_devices: [],
  connection_type: null,
  qr_data: null,
  session_token: null
};

const defaultWorkflowStatus: WorkflowStatus = {
  executing: false,
  current_node: null
};

const defaultDeploymentStatus: DeploymentStatus = {
  isRunning: false,
  activeRuns: 0,
  status: 'idle'
};

const defaultWorkflowLock: WorkflowLock = {
  locked: false,
  workflow_id: null,
  locked_at: null,
  reason: null
};

const defaultWhatsAppStatus: WhatsAppStatus = {
  connected: false,
  has_session: false,
  running: false,
  pairing: false
};

const defaultTwitterStatus: TwitterStatus = {
  connected: false,
  username: null,
  user_id: null,
};

const defaultGoogleStatus: GoogleStatus = {
  connected: false,
  email: null,
};

const defaultTelegramStatus: TelegramStatus = {
  connected: false,
  bot_username: null,
  bot_name: null,
  bot_id: null,
  owner_chat_id: null,
};

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

// WebSocket URL (convert http to ws)
const getWebSocketUrl = () => {
  const baseUrl = API_CONFIG.PYTHON_BASE_URL;

  // Production: empty base URL means use current origin
  if (!baseUrl) {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    return `${wsProtocol}://${window.location.host}/ws/status`;
  }

  // Development: convert http(s) to ws(s)
  const wsProtocol = baseUrl.startsWith('https') ? 'wss' : 'ws';
  const wsUrl = baseUrl.replace(/^https?/, wsProtocol);
  return `${wsUrl}/ws/status`;
};

// Max number of WhatsApp messages to keep in history
const MAX_WHATSAPP_MESSAGE_HISTORY = 100;

export const WebSocketProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // Get authentication state - only connect WebSocket when authenticated
  const { isAuthenticated, isLoading: authLoading } = useAuth();

  // Get current workflow ID for filtering node status updates (n8n pattern)
  const currentWorkflow = useAppStore(state => state.currentWorkflow);
  const currentWorkflowId = currentWorkflow?.id;

  const [isConnected, setIsConnected] = useState(false);
  // `isReady` flips true only AFTER the init burst inside `ws.onopen`
  // completes (api-key probes, terminal/chat/console history). Queries
  // that depend on backend-served catalogue data (NodeSpec catalogue,
  // node groups, node parameters, user settings, credential panels)
  // gate on `isReady` instead of `isConnected` so they fire once,
  // post-burst, instead of racing the serial awaits and arriving in
  // arbitrary order.
  const [isReady, setIsReady] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [androidStatus, setAndroidStatus] = useState<AndroidStatus>(defaultAndroidStatus);
  const [whatsappStatus, setWhatsappStatus] = useState<WhatsAppStatus>(defaultWhatsAppStatus);
  const [twitterStatus, setTwitterStatus] = useState<TwitterStatus>(defaultTwitterStatus);
  const [googleStatus, setGoogleStatus] = useState<GoogleStatus>(defaultGoogleStatus);
  const [telegramStatus, setTelegramStatus] = useState<TelegramStatus>(defaultTelegramStatus);
  const [whatsappMessages, setWhatsappMessages] = useState<WhatsAppMessage[]>([]);
  const [lastWhatsAppMessage, setLastWhatsAppMessage] = useState<WhatsAppMessage | null>(null);
  const [apiKeyStatuses, setApiKeyStatuses] = useState<Record<string, ApiKeyStatus>>({});
  const [consoleLogs, setConsoleLogs] = useState<ConsoleLogEntry[]>([]);
  const [terminalLogs, setTerminalLogs] = useState<TerminalLogEntry[]>([]);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  // Per-workflow node statuses live in a dedicated Zustand store
  // (`stores/nodeStatusStore.ts`). The store is built on
  // useSyncExternalStore so consumers can subscribe to a single
  // node's slot without re-rendering on unrelated status updates.
  // The derived `nodeStatuses` and `getNodeStatus` exposed below read
  // through to the store for backward compatibility.
  const [nodeParameters, setNodeParameters] = useState<Record<string, NodeParameters>>({});
  // Per-workflow variables: workflow_id -> variable_name -> value (n8n pattern)
  const [allVariables, setAllVariables] = useState<Record<string, Record<string, any>>>({});
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatus>(defaultWorkflowStatus);
  const [deploymentStatus, setDeploymentStatus] = useState<DeploymentStatus>(defaultDeploymentStatus);
  const [workflowLock, setWorkflowLock] = useState<WorkflowLock>(defaultWorkflowLock);
  // Per-workflow compaction stats: workflow_id -> session_id -> CompactionStats (n8n pattern)
  const [allCompactionStats, setAllCompactionStats] = useState<Record<string, Record<string, CompactionStats>>>({});

  // PartySocket's `ReconnectingWebSocket` implements the native WebSocket
  // surface (`send`, `close`, `readyState`, `addEventListener`, `onopen`,
  // `onmessage`, `onclose`, `onerror`) so consumers — including the request
  // correlation map and ping loop below — work unchanged. The ref's element
  // type tightens to the library's class so any feature-specific calls
  // (`shouldReconnect`, `reconnect()`) type-check.
  const wsRef = useRef<ReconnectingWebSocket | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingRequestsRef = useRef<Map<string, PendingRequest>>(new Map());
  // Generic broadcast subscribers ({type -> Set<handler>}) for events
  // surfaced via send_custom_event on the backend. Lets new features
  // listen for ad-hoc broadcasts without growing the switch statement
  // or the context state shape.
  const eventListenersRef = useRef<Map<string, Set<(data: any) => void>>>(new Map());
  // Pending-send queue for backpressure + replay across reconnects.
  // Drained inside `ws.onopen` after the init burst. Source of truth
  // for currentWorkflowId is `useAppStore.getState().currentWorkflow?.id`
  // (read via Zustand's documented escape hatch in non-React listeners).
  const pendingSendQueueRef = useRef<Array<QueuedSend>>([]);
  // Tracks the previously-seen workflow id purely for the prev-vs-current
  // comparison inside the workflow-switch effect below. NOT a global mirror;
  // do not read from elsewhere.
  const previousWorkflowIdForSwitchRef = useRef<string | undefined>(currentWorkflowId);

  // Detect workflow switches and refresh deployment status from the backend.
  // The single source of truth for currentWorkflowId is `useAppStore`; the
  // node-status store is synced from Dashboard.tsx, not from here.
  useEffect(() => {
    const previousWorkflowId = previousWorkflowIdForSwitchRef.current;

    // No need to clear node statuses - they are now stored per-workflow (n8n pattern)
    // Each workflow's statuses are isolated in allNodeStatuses[workflow_id]
    if (previousWorkflowId && currentWorkflowId && previousWorkflowId !== currentWorkflowId) {

      // Fetch deployment status for the new workflow (n8n pattern)
      // This ensures the deploy button shows correct state when switching workflows
      if (wsRef.current?.readyState === ReconnectingWebSocket.OPEN) {
        const fetchDeploymentStatus = async () => {
          try {
            const requestId = generateRequestId();
            const response = await new Promise<any>((resolve, reject) => {
              const timeout = setTimeout(() => reject(new Error('Timeout')), 5000);

              const handler = (event: MessageEvent) => {
                try {
                  const msg = JSON.parse(event.data);
                  if (msg.request_id === requestId) {
                    clearTimeout(timeout);
                    wsRef.current?.removeEventListener('message', handler);
                    resolve(msg);
                  }
                } catch { /* swallow message-parse errors — invalid frames are ignored */ }
              };

              wsRef.current?.addEventListener('message', handler);
              wsRef.current?.send(JSON.stringify({
                type: 'get_deployment_status',
                request_id: requestId,
                workflow_id: currentWorkflowId
              }));
            });

            // Update deployment status based on response
            const isRunning = response.is_running || false;
            setDeploymentStatus({
              isRunning,
              activeRuns: response.active_runs || 0,
              status: isRunning ? 'running' : 'idle',
              workflow_id: response.workflow_id || null
            });

            // Sync with Zustand store's per-workflow isExecuting state (n8n pattern)
            // This ensures Dashboard's isExecuting reflects the actual backend state
            const { setWorkflowExecuting } = useAppStore.getState();
            setWorkflowExecuting(currentWorkflowId, isRunning);

            // Also update workflow lock based on deployment status (n8n pattern)
            // A running workflow should be locked
            setWorkflowLock({
              locked: isRunning,
              workflow_id: isRunning ? currentWorkflowId : null,
              locked_at: isRunning ? Date.now() : null,
              reason: isRunning ? 'Workflow is running' : null
            });
          } catch (err) {
            console.error('[WebSocket] Failed to fetch deployment status:', err);
          }
        };
        fetchDeploymentStatus();
      }
    }

    // Update at the END of the effect so the next run sees the prior id.
    previousWorkflowIdForSwitchRef.current = currentWorkflowId;
  }, [currentWorkflowId]);

  // Handle incoming messages
  const handleMessage = useCallback((event: MessageEvent) => {
    try {
      const message = JSON.parse(event.data);
      const { type, data, node_id, name, value, output, variables: varsUpdate, request_id } = message;

      // Handle request/response pattern - resolve pending requests
      if (request_id && pendingRequestsRef.current.has(request_id)) {
        const pending = pendingRequestsRef.current.get(request_id)!;
        if (pending.timeout) {
          clearTimeout(pending.timeout);
        }
        pendingRequestsRef.current.delete(request_id);
        pending.resolve(message);
        return; // Response handled, don't process as broadcast
      }

      switch (type) {
        case 'initial_status':
        case 'full_status':
          if (data) {
            if (data.android) setAndroidStatus(data.android);
            if (data.whatsapp) setWhatsappStatus(data.whatsapp);
            if (data.twitter) setTwitterStatus(data.twitter);
            if (data.google) setGoogleStatus(data.google);
            if (data.telegram) setTelegramStatus(data.telegram);
            if (data.api_keys) {
              // SquareNode and the credentials modal both read from this
              // context map — no query cache to warm/invalidate for API
              // keys (the canvas never holds decrypted keys).
              setApiKeyStatuses(data.api_keys);
            }
            // Node statuses from initial_status - group by workflow_id (n8n pattern)
            if (data.nodes) {
              const groupedStatuses: Record<string, Record<string, NodeStatus>> = {};
              for (const [nodeId, status] of Object.entries(data.nodes)) {
                const nodeStatus = status as NodeStatus;
                const wfId = nodeStatus?.workflow_id || 'unknown';
                if (!groupedStatuses[wfId]) groupedStatuses[wfId] = {};
                groupedStatuses[wfId][nodeId] = nodeStatus;
              }
              useNodeStatusStore.getState().mergeStatuses(groupedStatuses);
            }
            if (data.node_parameters) {
              setNodeParameters(data.node_parameters);
              // Warm the nodeParams query cache so MiddleSection's memory /
              // master-skill queries hit immediately on first open.
              for (const [nodeId, params] of Object.entries(data.node_parameters)) {
                queryClient.setQueryData(nodeParamsQueryKey(nodeId), params);
              }
            }
            // Variables from initial_status - group by workflow_id (n8n pattern)
            if (data.variables) {
              // Variables may come with workflow_id or need grouping
              const groupedVars: Record<string, Record<string, any>> = {};
              for (const [varName, varData] of Object.entries(data.variables)) {
                const wfId = (varData as any)?.workflow_id || 'unknown';
                if (!groupedVars[wfId]) groupedVars[wfId] = {};
                groupedVars[wfId][varName] = varData;
              }
              setAllVariables(prev => ({ ...prev, ...groupedVars }));
            }
            if (data.workflow) setWorkflowStatus(data.workflow);
            if (data.workflow_lock) setWorkflowLock(data.workflow_lock);
            // Handle deployment status from initial_status (n8n/Conductor pattern)
            if (data.deployment) {
              setDeploymentStatus({
                isRunning: data.deployment.isRunning || false,
                activeRuns: data.deployment.activeRuns || 0,
                status: data.deployment.status || 'idle'
              });
            }
            // Catalogue 'stored' flags derive from api_keys + oauth state;
            // re-sync once after the bulk status lands.
            invalidateCatalogue(queryClient);
          }
          break;

        case 'api_key_status':
          if (message.provider) {
            setApiKeyStatuses(prev => ({
              ...prev,
              [message.provider]: data
            }));
            // Sidebar catalogue's `stored` flag depends on server-side
            // api_keys + oauth state; refresh it.
            invalidateCatalogue(queryClient);
          }
          break;

        case 'credential_catalogue_updated': {
          // The backend wraps a CloudEvents v1.0 `WorkflowEvent` envelope
          // inside `data` (see server/services/status_broadcaster.py and
          // server/services/events/envelope.py). The legacy outer wire
          // key stays as the dispatch tag for back-compat, but the
          // envelope's `id` / `time` / nested `type` (e.g.
          // `credential.api_key.saved`) are now statically typed for any
          // future consumer that wants ordering, dedup, or fine-grained
          // glob dispatch via `matchesType()`.
          const event = data as WorkflowEvent<{ provider: string; customer_id?: string }>;
          // Today the only action is to refetch the catalogue; the
          // envelope is read for telemetry / future dispatch.
          void event;
          invalidateCatalogue(queryClient);
          break;
        }

        case 'workflow_lifecycle': {
          // CloudEvents-typed workflow lifecycle. ``.imported`` and
          // ``.renamed`` both invalidate the workflows query so every
          // connected client refreshes the sidebar. The renaming tab
          // already updated its in-memory store from the save response
          // — this broadcast covers other open tabs of the same user.
          const event = data as WorkflowEvent<{
            name?: string;
            slug?: string;
            old_slug?: string;
            node_count?: number;
            edge_count?: number;
          }>;
          const eventType = event?.type ?? '';
          if (eventType.endsWith('.imported') || eventType.endsWith('.renamed')) {
            void queryClient.invalidateQueries({ queryKey: WORKFLOWS_QUERY_KEY });
          }
          break;
        }

        case 'skill_lifecycle': {
          // CloudEvents-typed skill registry lifecycle from
          // server/nodes/skill/master_skill/_events.py. Stages
          // `skill.created` / `.updated` / `.deleted` invalidate the
          // user-skills + folder-skills caches so the Master Skill
          // panel and any agent's Connected Skills view refresh
          // across every connected client. `skill.content_saved`
          // and `.deleted` also drop the per-skill content cache so
          // the next read re-fetches fresh instructions.
          const event = data as WorkflowEvent<{
            name?: string;
            is_builtin?: boolean;
            skill?: unknown;
          }>;
          const name = event?.data?.name || event?.subject;
          void queryClient.invalidateQueries({ queryKey: ['userSkills'] });
          void queryClient.invalidateQueries({ queryKey: ['folderSkills'] });
          if (name && (event?.type?.endsWith('.content_saved') || event?.type?.endsWith('.deleted'))) {
            queryClient.removeQueries({ queryKey: ['skillContent', name] });
          }
          break;
        }

        case 'plugin_connection_status': {
          // CloudEvents-typed connection-status envelope (Wave 12 B1-B3).
          // Backend dual-emits today: the legacy raw wire key
          // (android_status / whatsapp_status / telegram_status) keeps
          // the existing handlers below alive, while this case reads
          // from the typed envelope. After Wave 12 D4 drains the raw
          // siblings, the legacy cases below retire and this one
          // becomes the only path.
          //
          // Routing is by envelope.source substring — each plugin's
          // _events.py declares source="machinaos://nodes/<plugin>".
          const event = data as WorkflowEvent<Record<string, unknown>>;
          const source = event?.source || '';
          const payload = (event?.data || {}) as Record<string, unknown>;
          if (source.includes('/android')) {
            setAndroidStatus({ ...defaultAndroidStatus, ...(payload as Partial<typeof defaultAndroidStatus>) });
          } else if (source.includes('/whatsapp')) {
            setWhatsappStatus({ ...defaultWhatsAppStatus, ...(payload as Partial<typeof defaultWhatsAppStatus>) });
            invalidateCatalogue(queryClient);
          } else if (source.includes('/telegram')) {
            setTelegramStatus({
              connected: Boolean(payload.connected),
              bot_username: (payload.bot_username as string | null) ?? null,
              bot_name: (payload.bot_name as string | null) ?? null,
              bot_id: (payload.bot_id as string | null) ?? null,
              owner_chat_id: (payload.owner_chat_id as number | null) ?? null,
            });
            invalidateCatalogue(queryClient);
          }
          break;
        }

        case 'twitter_oauth_complete':
          // Handle Twitter OAuth completion broadcast from backend
          if (data?.success) {
            setTwitterStatus({
              connected: true,
              username: data.username || null,
              user_id: data.user_id || null,
              name: data.name,
              profile_image_url: data.profile_image_url,
            });
            invalidateCatalogue(queryClient);
          }
          break;

        case 'google_oauth_complete':
          // Handle Google Workspace OAuth completion broadcast from backend
          if (data?.success) {
            setGoogleStatus({
              connected: true,
              email: data.email || null,
              name: data.name,
              profile_image_url: data.profile_image_url,
            });
            invalidateCatalogue(queryClient);
          }
          break;

        case 'google_status':
          // Handle Google Workspace status update (refresh, logout)
          if (data) {
            setGoogleStatus({
              connected: data.connected || false,
              email: data.email || null,
              name: data.name,
            });
            invalidateCatalogue(queryClient);
          }
          break;

        case 'whatsapp_message_received':
          // Handle incoming WhatsApp message from Go service
          if (data) {
            const message: WhatsAppMessage = {
              message_id: data.message_id || data.id || '',
              sender: data.sender || data.from || '',
              chat_id: data.chat_id || data.chat || '',
              type: data.type || 'text',
              text: data.text || data.message || data.body || '',
              timestamp: data.timestamp || Date.now(),
              is_group: data.is_group || data.isGroup || false,
              push_name: data.push_name || data.pushName || data.name,
              media_url: data.media_url || data.mediaUrl,
              media_data: data.media_data || data.mediaData,
              caption: data.caption,
              latitude: data.latitude,
              longitude: data.longitude,
              contact_name: data.contact_name || data.contactName,
              vcard: data.vcard
            };

            // Update last message
            setLastWhatsAppMessage(message);

            // Add to message history (newest first, limit size)
            setWhatsappMessages(prev => {
              const updated = [message, ...prev];
              return updated.slice(0, MAX_WHATSAPP_MESSAGE_HISTORY);
            });

          }
          break;

        case 'node_status':
          // Per-workflow node status storage (n8n pattern)
          // Store status under workflow_id -> node_id structure
          if (node_id) {
            const statusWorkflowId = message.workflow_id || 'unknown';
            // Phase and tool_name are inside data.data (nested structure from broadcaster)
            const innerData = data?.data || {};

            // Flatten the structure: merge inner data with outer data for easier access
            const flattenedData = { ...data, ...innerData, workflow_id: statusWorkflowId };

            useNodeStatusStore.getState().setStatus(
              statusWorkflowId,
              node_id,
              flattenedData,
            );
          }
          break;

        case 'node_output':
          // Per-workflow node output storage (n8n pattern)
          if (node_id) {
            const outputWorkflowId = message.workflow_id || 'unknown';
            const store = useNodeStatusStore.getState();
            const previous =
              store.allStatuses[outputWorkflowId]?.[node_id] || ({} as NodeStatus);
            store.setStatus(outputWorkflowId, node_id, {
              ...previous,
              output,
              workflow_id: outputWorkflowId,
            });
          }
          break;

        case 'agent_progress': {
          // CloudEvents v1.0 envelope from broadcaster.broadcast_agent_progress.
          // Inner payload: { node_id, iteration, max_iterations, phase? }.
          // Routes into nodeStatusStore (same per-workflow / per-node slot
          // the existing useNodeStatus consumers read) so the AI-agent body
          // can render "iteration / max_iterations" live without a parallel
          // store. Wire-key parity with `credential_catalogue_updated`.
          const envelope = data as WorkflowEvent<{
            node_id?: string;
            iteration?: number;
            max_iterations?: number;
            phase?: string;
          }> | undefined;
          const inner = envelope?.data;
          const targetNodeId = inner?.node_id || envelope?.subject;
          const progressWorkflowId =
            envelope?.workflow_id || message.workflow_id || 'unknown';
          if (targetNodeId && inner) {
            const store = useNodeStatusStore.getState();
            const previous =
              store.allStatuses[progressWorkflowId]?.[targetNodeId] ||
              ({} as NodeStatus);
            // Defensive: an agent_progress event implies the agent IS
            // mid-loop. Set status='executing' even if no prior
            // node_status broadcast arrived first (race or edge case
            // where the agent finishes in a single step). Without this,
            // AIAgentNode's `isExecuting && iteration != null` gate
            // hides the badge entirely.
            const carriedStatus =
              previous.status === 'success' || previous.status === 'error'
                ? previous.status
                : 'executing';
            store.setStatus(progressWorkflowId, targetNodeId, {
              ...previous,
              status: carriedStatus,
              workflow_id: progressWorkflowId,
              data: {
                ...(previous.data || {}),
                iteration: inner.iteration,
                max_iterations: inner.max_iterations,
                ...(inner.phase ? { phase: inner.phase } : {}),
              },
            });
          }
          break;
        }

        case 'node_status_cleared':
          // Handle broadcast from server when node status is cleared
          if (node_id || message.node_id) {
            const clearedNodeId = node_id || message.node_id;
            const clearWorkflowId = message.workflow_id;
            const store = useNodeStatusStore.getState();
            if (clearWorkflowId) {
              store.clearStatus(clearWorkflowId, clearedNodeId);
            } else {
              // Clear from every workflow's slot
              for (const wfId of Object.keys(store.allStatuses)) {
                store.clearStatus(wfId, clearedNodeId);
              }
            }
          }
          break;

        // CloudEvents v1.0 envelope from `broadcaster.broadcast_node_parameters_updated`.
        // Wire shape (RFC §6.4 — same convention as `agent_progress` above):
        //   { type: "node_parameters_updated",
        //     data: { specversion, id, time, source, type,
        //             subject: <node_id>, workflow_id?,
        //             data: { node_id, parameters, version, source } } }
        // Snake-case keys preserved across the Python → JSON → TS wire so
        // the FE reads them by their Python field names directly. The
        // pre-CloudEvent handler read `message.parameters` / `message.node_id`
        // from the top level — both `undefined` post-cutover — so every
        // broadcast was silently dropped and the simpleMemory panel only
        // refreshed after a full page reload. Three emission sites today:
        // user param-save (source="user"), Claude Code memory bridge
        // (source="cli"), Temporal AgentWorkflow per-turn persist
        // (source="agent").
        case 'node_parameters_updated': {
          const envelope = data as WorkflowEvent<{
            node_id?: string;
            parameters?: Record<string, any>;
            version?: number;
            source?: string;
          }> | undefined;
          const inner = envelope?.data;
          const target_node_id = inner?.node_id || envelope?.subject;
          if (target_node_id && inner?.parameters !== undefined) {
            const next_params: NodeParameters = {
              parameters: inner.parameters,
              version: inner.version ?? 1,
            };
            setNodeParameters(prev => ({ ...prev, [target_node_id]: next_params }));
            queryClient.setQueryData(nodeParamsQueryKey(target_node_id), next_params);
          }
          break;
        }

        case 'node_parameters_deleted':
          if (node_id) {
            setNodeParameters(prev => {
              const updated = { ...prev };
              delete updated[node_id];
              return updated;
            });
            queryClient.removeQueries({ queryKey: nodeParamsQueryKey(node_id) });
          }
          break;

        case 'variable_update':
          // Per-workflow variable storage (n8n pattern)
          if (name !== undefined) {
            const varWorkflowId = message.workflow_id || 'unknown';
            setAllVariables((prev: Record<string, Record<string, any>>) => ({
              ...prev,
              [varWorkflowId]: {
                ...(prev[varWorkflowId] || {}),
                [name]: value
              }
            }));
          }
          break;

        case 'variables_update':
          // Per-workflow batch variable update (n8n pattern)
          if (varsUpdate) {
            const batchWorkflowId = message.workflow_id || 'unknown';
            setAllVariables((prev: Record<string, Record<string, any>>) => ({
              ...prev,
              [batchWorkflowId]: {
                ...(prev[batchWorkflowId] || {}),
                ...varsUpdate
              }
            }));
          }
          break;

        case 'workflow_status': {
          // Backend broadcasts this for both ad-hoc executions (execute_node /
          // execute_workflow) and explicit cancels. The active-run counter
          // logic in StatusBroadcaster guarantees that we receive an
          // `executing=true` once when work starts and an `executing=false`
          // once when the last concurrent run finishes -- safe to fan out
          // directly to per-workflow Zustand state without dedup.
          setWorkflowStatus(data || defaultWorkflowStatus);
          const wfId = message.workflow_id || data?.workflow_id;
          if (wfId && data && typeof data.executing === 'boolean') {
            const { setWorkflowExecuting } = useAppStore.getState();
            setWorkflowExecuting(wfId, data.executing);
          }
          break;
        }

        case 'deployment_snapshot': {
          // CloudEvents v1.0 envelope from broadcaster._send_deployment_snapshot.
          // Pushed once per WS connect so the FE can reconcile its stale
          // `deploymentStatus.isRunning=true` after a backend restart that
          // wiped DeploymentManager._deployments. Empty list is meaningful
          // and forces a reset — the prior bug was: backend restart wiped
          // the deployment dict, FE never got a `stopped` broadcast (because
          // there was nothing to broadcast about), so the Start button
          // stayed showing "Stop" forever.
          const envelope = data as WorkflowEvent<{
            running_workflow_ids?: string[];
          }> | undefined;
          const runningIds = envelope?.data?.running_workflow_ids ?? [];
          const runningSet = new Set(runningIds);
          const store = useAppStore.getState();
          const currentId = store.currentWorkflow?.id;

          // Reconcile per-workflow execution state in workflowUIStates.
          // Anything currently flagged isExecuting=true that isn't in
          // the snapshot's running set gets cleared (the load-bearing
          // reset for stale state after backend restart). Anything in
          // the snapshot gets flagged true.
          const existingStates = store.workflowUIStates ?? {};
          for (const [wid, ui] of Object.entries(existingStates)) {
            if (ui?.isExecuting && !runningSet.has(wid)) {
              store.setWorkflowExecuting(wid, false);
            }
          }
          for (const wid of runningIds) {
            store.setWorkflowExecuting(wid, true);
          }

          // Reconcile the toolbar `deploymentStatus` for the active workflow
          setDeploymentStatus(prev => {
            const next: DeploymentStatus = { ...prev };
            if (currentId && runningSet.has(currentId)) {
              next.isRunning = true;
              next.status = 'running';
              next.workflow_id = currentId;
            } else if (currentId && !runningSet.has(currentId) && prev.workflow_id === currentId) {
              // Previously thought current workflow was deployed; backend says no.
              next.isRunning = false;
              next.status = 'stopped';
              next.workflow_id = null;
              next.activeRuns = 0;
            }
            return next;
          });
          break;
        }

        case 'deployment_status':
          // Handle deployment status updates (event-driven, no iterations)
          // Per-workflow scoping (n8n pattern): Only apply updates for current workflow
          if (message.status) {
            const deploymentWorkflowId = message.workflow_id;
            const activeWorkflowId = useAppStore.getState().currentWorkflow?.id;

            // Apply deployment update if:
            // 1. It's for the current workflow, OR
            // 2. It's a stop/cancel/error (affects any workflow that was running), OR
            // 3. No specific workflow context (backward compatibility)
            const isTerminalStatus = ['stopped', 'cancelled', 'error'].includes(message.status);
            const shouldApplyDeployment = !deploymentWorkflowId ||
                                           deploymentWorkflowId === activeWorkflowId ||
                                           isTerminalStatus;

            if (shouldApplyDeployment) {
              setDeploymentStatus(prev => {
                const newStatus: DeploymentStatus = { ...prev };
                // Capture workflow_id from message
                if (message.workflow_id) {
                  newStatus.workflow_id = message.workflow_id;
                }

                switch (message.status) {
                  case 'starting':
                    newStatus.isRunning = true;
                    newStatus.status = 'starting';
                    newStatus.activeRuns = 0;
                    break;
                  case 'running':
                  case 'started':
                    newStatus.isRunning = true;
                    newStatus.status = 'running';
                    newStatus.activeRuns = message.data?.active_runs ?? prev.activeRuns;
                    break;
                  case 'run_started':
                    newStatus.isRunning = true;
                    newStatus.status = 'running';
                    newStatus.activeRuns = message.data?.active_runs || prev.activeRuns + 1;
                    break;
                  case 'run_complete':
                    newStatus.activeRuns = Math.max(0, message.data?.active_runs || prev.activeRuns - 1);
                    break;
                  case 'stopped':
                    // Only clear if this was our workflow or no workflow was tracked
                    if (!prev.workflow_id || prev.workflow_id === deploymentWorkflowId) {
                      newStatus.isRunning = false;
                      newStatus.status = 'stopped';
                      newStatus.totalTime = message.data?.total_time;
                      newStatus.activeRuns = 0;
                      newStatus.workflow_id = null;
                    }
                    break;
                  case 'cancelled':
                    // Only clear if this was our workflow or no workflow was tracked
                    if (!prev.workflow_id || prev.workflow_id === deploymentWorkflowId) {
                      newStatus.isRunning = false;
                      newStatus.status = 'cancelled';
                      newStatus.activeRuns = 0;
                      newStatus.workflow_id = null;
                    }
                    break;
                  case 'error':
                    // Only clear if this was our workflow or no workflow was tracked
                    if (!prev.workflow_id || prev.workflow_id === deploymentWorkflowId) {
                      newStatus.isRunning = false;
                      newStatus.status = 'error';
                      newStatus.error = message.error;
                      newStatus.workflow_id = null;
                    }
                    break;
                }

                return newStatus;
              });
              // Sync with Zustand store's per-workflow isExecuting state (n8n pattern)
              if (deploymentWorkflowId) {
                const { setWorkflowExecuting } = useAppStore.getState();
                const isRunning = ['starting', 'running', 'started', 'run_started'].includes(message.status);
                const isStopped = ['stopped', 'cancelled', 'error'].includes(message.status);
                if (isRunning || isStopped) {
                  setWorkflowExecuting(deploymentWorkflowId, isRunning);
                }
              }
            }
          }
          break;

        case 'pong':
          // Keep-alive response, no action needed
          break;

        case 'console_log':
          // Handle console log entries from Console nodes. Scope to the
          // currently-open workflow: a log carrying a different workflow_id
          // belongs to a parallel run the user isn't viewing right now,
          // and must not bleed into this panel. Logs without workflow_id
          // (legacy / non-workflow contexts) still render so we don't
          // hide debug output during transition.
          if (data) {
            const activeWorkflowId = useAppStore.getState().currentWorkflow?.id;
            if (
              data.workflow_id &&
              activeWorkflowId &&
              data.workflow_id !== activeWorkflowId
            ) {
              break;
            }
            const logEntry: ConsoleLogEntry = {
              node_id: data.node_id || '',
              label: data.label || 'Console',
              timestamp: data.timestamp || new Date().toISOString(),
              data: data.data,
              formatted: data.formatted || JSON.stringify(data.data, null, 2),
              format: data.format || 'json',
              workflow_id: data.workflow_id,
              source_node_id: data.source_node_id,
              source_node_type: data.source_node_type,
              source_node_label: data.source_node_label
            };
            // Add to logs (newest first, limit to 100 entries)
            setConsoleLogs(prev => {
              const updated = [logEntry, ...prev];
              return updated.slice(0, 100);
            });
          }
          break;

        case 'console_logs_cleared':
          // Handle console logs cleared from server
          if (message.workflow_id) {
            setConsoleLogs(prev => prev.filter(log => log.workflow_id !== message.workflow_id));
          } else {
            setConsoleLogs([]);
          }
          break;

        case 'terminal_log':
          // Handle terminal/server log entries
          if (data) {
            const terminalEntry: TerminalLogEntry = {
              timestamp: data.timestamp || new Date().toISOString(),
              level: data.level || 'info',
              message: data.message || '',
              source: data.source,
              details: data.details
            };
            // Add to logs (newest first, limit to 200 entries)
            setTerminalLogs(prev => {
              const updated = [terminalEntry, ...prev];
              return updated.slice(0, 200);
            });
          }
          break;

        case 'terminal_logs_cleared':
          // Handle terminal logs cleared from server
          setTerminalLogs([]);
          break;

        case 'workflow_lock':
          // Handle workflow lock status updates (per-workflow locking - n8n pattern)
          // Only update lock state if it's for the current workflow or if unlocking
          if (data) {
            const lockWorkflowId = message.workflow_id || data.workflow_id;
            const activeWorkflowId = useAppStore.getState().currentWorkflow?.id;

            // Apply lock update if:
            // 1. It's for the current workflow, OR
            // 2. We're unlocking (locked=false), OR
            // 3. No specific workflow context (backward compatibility)
            const shouldApplyLock = !lockWorkflowId ||
                                     lockWorkflowId === activeWorkflowId ||
                                     !data.locked;

            if (shouldApplyLock) {
              setWorkflowLock({
                locked: data.locked || false,
                workflow_id: data.workflow_id || null,
                locked_at: data.locked_at || null,
                reason: data.reason || null
              });
            }
          }
          break;

        case 'token_usage_update': {
          // Real-time token usage from backend after each AI execution
          const tokenSessionId = message.session_id;
          const tokenWorkflowId = message.workflow_id || useAppStore.getState().currentWorkflow?.id || '';
          const trackingData = message.data || {};
          if (tokenSessionId && tokenWorkflowId) {
            setAllCompactionStats(prev => {
              const wfStats = prev[tokenWorkflowId] || {};
              const existing = wfStats[tokenSessionId];
              return {
                ...prev,
                [tokenWorkflowId]: {
                  ...wfStats,
                  [tokenSessionId]: {
                    session_id: tokenSessionId,
                    total: trackingData.total ?? existing?.total ?? 0,
                    threshold: trackingData.threshold ?? existing?.threshold ?? 0,
                    count: existing?.count ?? 0,
                    total_cost: trackingData.total_cost ?? existing?.total_cost,
                  }
                }
              };
            });
            // Session-scoped invalidation: the query key also carries
            // model + provider, which aren't in the broadcast payload, so
            // we invalidate the whole namespace and let the ~1-2 mounted
            // compaction queries refetch fresh values.
            queryClient.invalidateQueries({ queryKey: queryKeys.compactionStats._def });
          }
          break;
        }

        case 'compaction_completed': {
          // Backend completed compaction - increment count if successful
          const compSessionId = message.session_id;
          if (compSessionId) {
            setAllCompactionStats(prev => {
              const updated = { ...prev };
              for (const wid of Object.keys(updated)) {
                if (updated[wid]?.[compSessionId]) {
                  updated[wid] = {
                    ...updated[wid],
                    [compSessionId]: {
                      ...updated[wid][compSessionId],
                      count: (updated[wid][compSessionId].count || 0) + (message.success ? 1 : 0),
                      total: message.tokens_after ?? updated[wid][compSessionId].total,
                    }
                  };
                }
              }
              return updated;
            });
            queryClient.invalidateQueries({ queryKey: queryKeys.compactionStats._def });
          }
          break;
        }

        case 'compaction_starting':
          // Could be used for UI loading indicator in the future
          break;

        case 'error':
          console.error('[WebSocket] Server error:', message.code, message.message);
          break;

        default: {
          // Generic broadcast dispatch -- any listener registered via
          // addEventListener(type, handler) gets the message data. Lets
          // backend-only features (e.g. workflow_ops_apply) ship without
          // adding a switch case + state slice every time.
          const listeners = eventListenersRef.current.get(type);
          if (listeners && listeners.size > 0) {
            for (const handler of listeners) {
              try { handler(data); } catch (err) {
                console.error(`[WebSocket] Listener for '${type}' threw:`, err);
              }
            }
          }
          break;
        }
      }
    } catch (error) {
      console.error('[WebSocket] Failed to parse message:', error);
    }
  }, []);  // Empty deps - reads workflow id via useAppStore.getState() escape hatch

  // Drain queued sends after a successful reconnect. Each queued send gets a
  // fresh request_id and a reset timeout budget; responses correlate via the
  // existing pendingRequestsRef map. Per-call abortController cancels the
  // queue-side timeout that was running while the request was waiting in line.
  const drainPendingSends = useCallback((ws: ReconnectingWebSocket) => {
    const queue = pendingSendQueueRef.current;
    pendingSendQueueRef.current = [];
    for (const queued of queue) {
      try {
        queued.abortController.abort();
      } catch {
        // Ignore — abort is idempotent.
      }
      if (ws.readyState !== WebSocket.OPEN) {
        // Defensive: should not happen since we drain inside onopen.
        try {
          queued.reject(new Error('WebSocket closed during drain'));
        } catch {
          // Ignore — caller may have already settled.
        }
        continue;
      }
      const requestId = generateRequestId();
      let timeout: ReturnType<typeof setTimeout> | null = null;
      if (queued.timeoutMs > 0) {
        // Reset the timeout budget on replay so caller's perspective
        // remains "now"; queue-side timer was already aborted above.
        timeout = setTimeout(() => {
          pendingRequestsRef.current.delete(requestId);
          queued.reject(new Error(`Request timeout: ${queued.type}`));
        }, queued.timeoutMs);
      }
      pendingRequestsRef.current.set(requestId, {
        resolve: queued.resolve,
        reject: queued.reject,
        timeout,
      });
      ws.send(JSON.stringify({
        type: queued.type,
        request_id: requestId,
        ...queued.data,
      }));
    }
  }, []);

  // Connect to WebSocket. Uses PartySocket's `ReconnectingWebSocket` —
  // a native-WebSocket-compatible class with built-in jittered exponential
  // backoff, message replay, and intentional-close (code 1000) handling.
  // Replaces the previous flat 3 s `setTimeout(reconnect)` loop.
  // Backoff envelope is configured via `WS_RECONNECT` in
  // `lib/connectionConfig.ts` so a future tuning pass is a one-file edit.
  // Ref: https://docs.partykit.io/reference/partysocket-api/
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === ReconnectingWebSocket.OPEN) {
      return;
    }

    const wsUrl = getWebSocketUrl();

    try {
      const ws = new ReconnectingWebSocket(wsUrl, [], {
        minReconnectionDelay: WS_RECONNECT.MIN_DELAY_MS,
        maxReconnectionDelay: WS_RECONNECT.MAX_DELAY_MS,
        reconnectionDelayGrowFactor: WS_RECONNECT.GROW_FACTOR,
        // Reconnect indefinitely while the page is open. Intentional
        // closes via `ws.close(1000, ...)` (logout / unmount) skip the
        // reconnect path because PartySocket inspects the close code.
        maxRetries: Infinity,
        // Send-while-disconnected buffer; replayed automatically on the
        // next OPEN. Mirrors the previous `pendingSendQueueRef` cap intent
        // for opportunistic out-of-band sends.
        maxEnqueuedMessages: WS_RECONNECT.MAX_ENQUEUED_MESSAGES,
      });

      ws.onopen = async () => {
        setIsConnected(true);
        setReconnecting(false);

        // NodeSpec + NodeGroups are immutable for the life of a page —
        // they only change on a backend redeploy, and a redeploy
        // bumps the build version which busts the persisted cache on
        // the next hard refresh (see lib/queryPersist.ts). Reconnects
        // alone (transient network drops, server restarts mid-deploy)
        // do not invalidate them, so canvas / palette icons no longer
        // flash back to fallback on every reconnect.

        // Start ping interval
        pingIntervalRef.current = setInterval(() => {
          if (ws.readyState === ReconnectingWebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30000);

        // Drain any sends that were queued while the socket was reconnecting.
        // Runs BEFORE setIsReady(true) so isReady-gated callers don't race
        // an empty pendingRequestsRef. Mirrors socket.io-client's offline
        // buffer + Apollo RetryLink semantics.
        drainPendingSends(ws);

        // Wave 32: flip `isReady` IMMEDIATELY on socket open + queue drain.
        // The page-state restore (terminal / chat / console history) fires
        // in the BACKGROUND below — its results trickle into state writes
        // when each request settles, but UI interaction is no longer blocked
        // behind a serial 5-up-to-25-second `Promise.allSettled` await.
        //
        // Why this matters: tab-blur + WS reconnect previously left the user
        // staring at an unresponsive workflow until init-burst finished.
        // First click "did nothing" because catalogue / nodeSpec / credentials
        // queries gate on `isReady` and stayed disabled. The cache (warmed
        // from localStorage via PersistQueryClientProvider for `nodeSpec` /
        // `nodeGroups` / `pluginCatalogue` / `skillContent`) carries the
        // visible state until refreshes land.
        //
        // Wave 32 also dropped the legacy hardcoded `probeApiKey` loop over
        // `['openai', 'anthropic', 'gemini', 'google_maps', 'android_remote']`.
        // Those probes were redundant — credential state has TWO authoritative
        // sources already:
        //   1. The backend's `initial_status` broadcast (handled at line ~638)
        //      pushes the full `api_keys` map on every reconnect.
        //   2. The catalogue (TanStack Query `useCatalogueQuery`) carries the
        //      `provider.stored` flag for every provider; refetched via the
        //      debounced `invalidateCatalogue(queryClient)` helper that 8+
        //      credential CloudEvent handlers already fire (`api_key_status`,
        //      `credential_catalogue_updated`, `whatsapp_status`,
        //      `twitter_oauth_complete`, `google_oauth_complete`,
        //      `google_status`, `telegram_status`, `initial_status`).
        // No frontend should hardcode a provider list — adding a new
        // provider should be a backend-only edit.
        setIsReady(true);

        // Single-shot catalogue invalidate so any credential mutations that
        // landed on the server while the socket was disconnected propagate
        // to the in-memory cache immediately. Debounced (300ms trailing) so
        // it coalesces with other broadcast-driven invalidations.
        invalidateCatalogue(queryClient);

        // Page-state restore (background). Fire-and-forget — these writes
        // hydrate panels that PersistQueryClient doesn't cache (terminal /
        // chat / console history come straight from the server's per-request
        // log read, not from a query cache).
        const sendBurstRequest = <T = any>(payload: object, idPrefix: string): Promise<T> =>
          new Promise<T>((resolve, reject) => {
            const requestId = `${idPrefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
            const timeout = setTimeout(() => {
              ws.removeEventListener('message', handler);
              reject(new Error('Timeout'));
            }, 5000);
            const handler = (event: MessageEvent) => {
              try {
                const msg = JSON.parse(event.data);
                if (msg.request_id === requestId) {
                  clearTimeout(timeout);
                  ws.removeEventListener('message', handler);
                  resolve(msg);
                }
              } catch { /* swallow message-parse errors — invalid frames are ignored */ }
            };
            ws.addEventListener('message', handler);
            ws.send(JSON.stringify({ ...payload, request_id: requestId }));
          });

        void (async () => {
          try {
            const terminalResponse = await sendBurstRequest<any>(
              { type: 'get_terminal_logs' },
              'terminal_logs',
            );
            if (terminalResponse.success && terminalResponse.logs) {
              const logs: TerminalLogEntry[] = terminalResponse.logs.map((log: any) => ({
                timestamp: log.timestamp || new Date().toISOString(),
                level: log.level || 'info',
                message: log.message || '',
                source: log.source,
                details: log.details,
              })).reverse();
              setTerminalLogs(logs);
            }
          } catch {
            // Ignore errors loading terminal logs
          }
        })();

        void (async () => {
          try {
            const workflowId = useAppStore.getState().currentWorkflow?.id || 'default';
            const chatResponse = await sendBurstRequest<any>(
              { type: 'get_chat_messages', session_id: workflowId },
              'chat_messages',
            );
            if (chatResponse.success && chatResponse.messages) {
              const messages: ChatMessage[] = chatResponse.messages.map((msg: any) => ({
                role: msg.role as 'user' | 'assistant',
                message: msg.message,
                timestamp: msg.timestamp,
              }));
              setChatMessages(messages);
            }
          } catch {
            // Ignore errors loading chat messages
          }
        })();

        void (async () => {
          try {
            const consoleWorkflowId = useAppStore.getState().currentWorkflow?.id;
            const consoleResponse = await sendBurstRequest<any>(
              { type: 'get_console_logs', limit: 100, workflow_id: consoleWorkflowId },
              'console',
            );
            if (consoleResponse.success && consoleResponse.logs) {
              const logs: ConsoleLogEntry[] = consoleResponse.logs.map((log: any) => ({
                node_id: log.node_id,
                label: log.label,
                timestamp: log.timestamp,
                data: log.data,
                formatted: log.formatted,
                format: log.format,
                workflow_id: log.workflow_id,
                source_node_id: log.source_node_id,
                source_node_type: log.source_node_type,
                source_node_label: log.source_node_label,
              }));
              setConsoleLogs(logs);
            }
          } catch {
            // Ignore errors loading console logs
          }
        })();
      };

      ws.onmessage = handleMessage;

      ws.onclose = (event) => {
        console.log('[WebSocket] Disconnected:', event.code, event.reason);
        setIsConnected(false);
        setIsReady(false);
        wsRef.current = null;

        // Reject every in-flight request so TanStack Query's retry +
        // any awaiters fail fast on the dead socket instead of waiting
        // for the 30s REQUEST_TIMEOUT. Trigger-node waiters that deliberately
        // had no timeout are still cleared — the next reconnect will
        // re-register them when the user re-runs the trigger.
        if (pendingRequestsRef.current.size > 0) {
          for (const [, pending] of pendingRequestsRef.current) {
            if (pending.timeout) clearTimeout(pending.timeout);
            try {
              pending.reject(new Error('WebSocket closed'));
            } catch {
              // Ignore — caller may have already settled.
            }
          }
          pendingRequestsRef.current.clear();
        }

        // Pending-send queue handling: only drop on intentional close
        // (RFC 6455 §7.4.1 Normal Closure, code 1000). Transient closes
        // preserve the queue so the next onopen drain replays them.
        if (event.code === WS_CLOSE.NORMAL_CLOSURE) {
          if (pendingSendQueueRef.current.length > 0) {
            for (const queued of pendingSendQueueRef.current) {
              try {
                queued.abortController.abort();
                queued.reject(new Error('WebSocket closed (intentional)'));
              } catch {
                // Ignore — caller may have already settled.
              }
            }
            pendingSendQueueRef.current = [];
          }
        }

        // Clear ping interval
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current);
          pingIntervalRef.current = null;
        }

        // PartySocket performs the reconnect itself when
        // `event.code !== WS_CLOSE.NORMAL_CLOSURE`, honouring the
        // `WS_RECONNECT` envelope passed at construction time. Surface
        // "reconnecting" to the UI for transient closes; intentional
        // closes (logout / unmount) leave the flag false.
        if (event.code !== WS_CLOSE.NORMAL_CLOSURE) {
          setReconnecting(true);
        }
      };

      ws.onerror = (error) => {
        console.error('[WebSocket] Error:', error);
      };

      wsRef.current = ws;
    } catch (error) {
      // Construction-time error (e.g. malformed URL). PartySocket has
      // not installed its retry loop yet, so just log — there is
      // nothing to reconnect to. The runtime path (network drops after
      // successful construction) is covered by PartySocket's internal
      // jittered backoff.
      console.error('[WebSocket] Failed to create connection:', error);
    }
  }, [handleMessage, drainPendingSends]);

  // Request current status
  const requestStatus = useCallback(() => {
    if (wsRef.current?.readyState === ReconnectingWebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'get_status' }));
    }
  }, []);

  // Get node status for current workflow (n8n pattern). Reads from the
  // node-status Zustand store which is the source of truth post Phase
  // 2.2; non-reactive snapshot read so this callback identity stays
  // stable across status writes. Components that need reactivity
  // should use the `useNodeStatus(id)` slice hook instead.
  const getNodeStatus = useCallback((nodeId: string) => {
    if (!currentWorkflowId) return undefined;
    return useNodeStatusStore
      .getState()
      .allStatuses[currentWorkflowId]?.[nodeId];
  }, [currentWorkflowId]);

  // Get API key status
  const getApiKeyStatus = useCallback((provider: string) => {
    return apiKeyStatuses[provider];
  }, [apiKeyStatuses]);

  // Get variable value for current workflow (n8n pattern)
  // IMPORTANT: Use currentWorkflowId state directly (not ref) to ensure reactivity on workflow switch
  const getVariable = useCallback((name: string) => {
    if (!currentWorkflowId) return undefined;
    return allVariables[currentWorkflowId]?.[name];
  }, [allVariables, currentWorkflowId]);

  // Clear node status (used when clearing execution results)
  // Also clears the backend node_outputs storage
  const clearNodeStatus = useCallback(async (nodeId: string) => {
    const workflowId = useAppStore.getState().currentWorkflow?.id;
    if (workflowId) {
      useNodeStatusStore.getState().clearStatus(workflowId, nodeId);
    }
    // Clear backend storage
    try {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({
          type: 'clear_node_output',
          node_id: nodeId,
          workflow_id: workflowId
        }));
      }
    } catch (err) {
      console.error('[WebSocket] Failed to clear backend node output:', err);
    }
  }, []);

  // Clear WhatsApp message history
  const clearWhatsAppMessages = useCallback(() => {
    setWhatsappMessages([]);
    setLastWhatsAppMessage(null);
  }, []);

  // Clear console logs (both local state and database). Scoped to
  // the currently-open workflow so other workflows' history survives.
  const clearConsoleLogs = useCallback(() => {
    setConsoleLogs([]);
    if (wsRef.current?.readyState === ReconnectingWebSocket.OPEN) {
      const workflowId = useAppStore.getState().currentWorkflow?.id;
      wsRef.current.send(JSON.stringify({
        type: 'clear_console_logs',
        workflow_id: workflowId,
      }));
    }
  }, []);

  // Clear terminal logs (also clears on server)
  const clearTerminalLogs = useCallback(() => {
    setTerminalLogs([]);
    // Also notify server to clear its terminal log history
    if (wsRef.current?.readyState === ReconnectingWebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'clear_terminal_logs' }));
    }
  }, []);

  // Clear chat messages (both local state and database). Scoped to
  // the currently-open workflow (session_id == workflow_id on the
  // chat side); other workflows' history survives.
  // Uses direct WebSocket send to avoid dependency on sendRequest (which is defined later).
  const clearChatMessages = useCallback(() => {
    setChatMessages([]);
    if (wsRef.current?.readyState === ReconnectingWebSocket.OPEN) {
      const workflowId = useAppStore.getState().currentWorkflow?.id || 'default';
      wsRef.current.send(JSON.stringify({
        type: 'clear_chat_messages',
        session_id: workflowId,
      }));
    }
  }, []);

  // Refetch chat + console panels when the user switches workflow.
  // Both are scoped on the backend (chat by session_id == workflow id,
  // console by workflow_id). Resets local state first so the panel
  // doesn't briefly show the previous workflow's content while the
  // refetch is in flight. The initial bootstrap inside ws.onopen runs
  // before this effect with a "default" / null id; this effect then
  // refires once ``currentWorkflowId`` resolves to a real value.
  useEffect(() => {
    if (!isReady || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return;
    }
    setChatMessages([]);
    setConsoleLogs([]);
    const sessionId = currentWorkflowId || 'default';
    const ws = wsRef.current;

    const chatRequestId = `chat_switch_${Date.now()}`;
    const consoleRequestId = `console_switch_${Date.now()}`;
    const handler = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.request_id === chatRequestId && msg.success && Array.isArray(msg.messages)) {
          setChatMessages(msg.messages.map((m: any) => ({
            role: m.role as 'user' | 'assistant',
            message: m.message,
            timestamp: m.timestamp,
          })));
        } else if (msg.request_id === consoleRequestId && msg.success && Array.isArray(msg.logs)) {
          setConsoleLogs(msg.logs.map((log: any) => ({
            node_id: log.node_id,
            label: log.label,
            timestamp: log.timestamp,
            data: log.data,
            formatted: log.formatted,
            format: log.format,
            workflow_id: log.workflow_id,
            source_node_id: log.source_node_id,
            source_node_type: log.source_node_type,
            source_node_label: log.source_node_label,
          })));
        }
      } catch {
        // Ignore parse errors — handler is best-effort.
      }
    };
    ws.addEventListener('message', handler);
    ws.send(JSON.stringify({ type: 'get_chat_messages', session_id: sessionId, request_id: chatRequestId }));
    ws.send(JSON.stringify({ type: 'get_console_logs', limit: 100, workflow_id: currentWorkflowId, request_id: consoleRequestId }));
    return () => {
      ws.removeEventListener('message', handler);
    };
  }, [currentWorkflowId, isReady]);

  // Derive current workflow's node statuses (n8n pattern). Subscribes
  // to the Zustand store so context consumers still re-render on
  // status changes; per-node consumers should prefer the `useNodeStatus`
  // slice hook (`useNodeStatusForId`) which only re-renders for its
  // specific slot.
  const nodeStatuses = useCurrentWorkflowStatuses();

  // Derive current workflow's variables (n8n pattern)
  // This provides a flat Record<varName, value> for the current workflow
  // IMPORTANT: Use currentWorkflowId state directly, not ref, to ensure re-render on workflow switch
  const variables = useMemo(() => {
    if (!currentWorkflowId) return {};
    return allVariables[currentWorkflowId] || {};
  }, [allVariables, currentWorkflowId]);

  // Derive current workflow's compaction stats (n8n pattern)
  const compactionStats = useMemo(() => {
    if (!currentWorkflowId) return {};
    return allCompactionStats[currentWorkflowId] || {};
  }, [allCompactionStats, currentWorkflowId]);

  // Update compaction stats for a specific workflow + session (called by MiddleSection on initial fetch)
  const updateCompactionStats = useCallback((workflowId: string, sessionId: string, stats: CompactionStats) => {
    setAllCompactionStats(prev => ({
      ...prev,
      [workflowId]: {
        ...(prev[workflowId] || {}),
        [sessionId]: stats,
      }
    }));
  }, []);

  // =========================================================================
  // Core Request/Response Pattern
  // =========================================================================

  // Send a request and wait for response.
  // timeoutMs: undefined/0 = use default, negative = no timeout (for trigger nodes).
  //
  // Fast path: socket open -> send immediately (legacy behaviour).
  // Slow path: socket closed/connecting -> enqueue with backpressure cap (FIFO
  //   eviction at QUEUE_MAX_SIZE) and replay on the next ws.onopen drain.
  // Per-request timeout is enforced both while queued (via AbortController-
  // backed setTimeout) and after replay (regular pendingRequestsRef timeout).
  const sendRequest = useCallback(async <T = any>(
    type: string,
    data?: Record<string, any>,
    timeoutMs?: number
  ): Promise<T> => {
    return new Promise((resolve, reject) => {
      const useTimeout = timeoutMs === undefined || timeoutMs >= 0;
      const actualTimeout = timeoutMs && timeoutMs > 0 ? timeoutMs : REQUEST_TIMEOUT;
      const effectiveTimeout = (timeoutMs === -1 || !useTimeout) ? -1 : actualTimeout;

      // FAST PATH: socket open — send immediately.
      if (wsRef.current?.readyState === ReconnectingWebSocket.OPEN) {
        const requestId = generateRequestId();
        let timeout: ReturnType<typeof setTimeout> | null = null;
        if (effectiveTimeout > 0) {
          timeout = setTimeout(() => {
            pendingRequestsRef.current.delete(requestId);
            reject(new Error(`Request timeout: ${type}`));
          }, effectiveTimeout);
        }
        pendingRequestsRef.current.set(requestId, { resolve, reject, timeout });
        wsRef.current.send(JSON.stringify({
          type,
          request_id: requestId,
          ...data,
        }));
        return;
      }

      // SLOW PATH: socket closed/connecting — queue with backpressure cap.
      // FIFO eviction: when at capacity, reject the oldest entry to make room.
      if (pendingSendQueueRef.current.length >= QUEUE_MAX_SIZE) {
        const oldest = pendingSendQueueRef.current.shift();
        if (oldest) {
          try {
            oldest.abortController.abort();
            oldest.reject(new Error('backpressure: too many queued requests'));
          } catch {
            // Ignore — caller may have already settled.
          }
        }
      }

      const abortController = new AbortController();
      const queued: QueuedSend = {
        type,
        data,
        resolve,
        reject,
        enqueuedAt: Date.now(),
        timeoutMs: effectiveTimeout,
        abortController,
      };
      pendingSendQueueRef.current.push(queued);

      // Per-request timeout while queued (only when finite). The drain helper
      // aborts this controller before replaying so the timeout doesn't fire
      // after the request is back in flight.
      if (effectiveTimeout > 0) {
        const queueTimeout = setTimeout(() => {
          const idx = pendingSendQueueRef.current.indexOf(queued);
          if (idx >= 0) {
            pendingSendQueueRef.current.splice(idx, 1);
            try {
              reject(new Error(`Request timeout (queued): ${type}`));
            } catch {
              // Ignore — caller may have already settled.
            }
          }
        }, effectiveTimeout);
        abortController.signal.addEventListener('abort', () => clearTimeout(queueTimeout));
      }
    });
  }, []);

  // =========================================================================
  // Chat Message Operations
  // =========================================================================

  // Send chat message (triggers chatTrigger nodes and saves to database)
  // nodeId: optional specific chatTrigger node to target
  const sendChatMessageAsync = useCallback(async (message: string, nodeId?: string): Promise<void> => {
    const timestamp = new Date().toISOString();
    const chatMessage: ChatMessage = {
      role: 'user',
      message,
      timestamp
    };

    // Add to local messages immediately for UI feedback
    setChatMessages(prev => [...prev, chatMessage]);

    // Send to backend to dispatch to chatTrigger nodes (also saves to database).
    // session_id is the workflow id when one is open, "default" otherwise --
    // this is what scopes the persisted chat history to a single workflow.
    try {
      const workflowId = useAppStore.getState().currentWorkflow?.id || 'default';
      await sendRequest('send_chat_message', {
        message,
        role: 'user',
        node_id: nodeId,  // Target specific chatTrigger node if specified
        session_id: workflowId,
        timestamp
      });
    } catch (error) {
      console.error('[WebSocket] Failed to send chat message:', error);
      throw error;
    }
  }, [sendRequest]);

  // =========================================================================
  // Node Parameters Operations
  // =========================================================================

  const getNodeParametersAsync = useCallback(async (nodeId: string): Promise<NodeParameters | null> => {
    try {
      const response = await sendRequest<any>('get_node_parameters', { node_id: nodeId });
      if (response.parameters) {
        const params: NodeParameters = {
          parameters: response.parameters,
          version: response.version || 0,
        };
        // Update local cache
        setNodeParameters(prev => ({ ...prev, [nodeId]: params }));
        return params;
      }
      return null;
    } catch (error) {
      console.error('[WebSocket] Failed to get node parameters:', error);
      return null;
    }
  }, [sendRequest]);

  const getAllNodeParametersAsync = useCallback(async (nodeIds: string[]): Promise<Record<string, NodeParameters>> => {
    if (!nodeIds.length) return {};
    try {
      const response = await sendRequest<any>('get_all_node_parameters', { node_ids: nodeIds });
      const result: Record<string, NodeParameters> = {};

      if (response.parameters) {
        for (const [nodeId, data] of Object.entries(response.parameters as Record<string, any>)) {
          result[nodeId] = {
            parameters: data.parameters || {},
            version: data.version || 0,
          };
        }
        // Update local cache with all parameters
        setNodeParameters(prev => ({ ...prev, ...result }));
      }
      return result;
    } catch (error) {
      console.error('[WebSocket] Failed to get all node parameters:', error);
      return {};
    }
  }, [sendRequest]);

  const saveNodeParametersAsync = useCallback(async (
    nodeId: string,
    parameters: Record<string, any>,
    version?: number
  ): Promise<boolean> => {
    try {
      const currentVersion = nodeParameters[nodeId]?.version || version || 0;
      const response = await sendRequest<any>('save_node_parameters', {
        node_id: nodeId,
        parameters,
        version: currentVersion
      });
      if (response.success !== false) {
        // Update local cache
        setNodeParameters(prev => ({
          ...prev,
          [nodeId]: {
            parameters: response.parameters || parameters,
            version: response.version || currentVersion + 1,
            timestamp: response.timestamp
          }
        }));
        return true;
      }
      return false;
    } catch (error) {
      console.error('[WebSocket] Failed to save node parameters:', error);
      return false;
    }
  }, [sendRequest, nodeParameters]);

  const deleteNodeParametersAsync = useCallback(async (nodeId: string): Promise<boolean> => {
    try {
      await sendRequest<any>('delete_node_parameters', { node_id: nodeId });
      setNodeParameters(prev => {
        const updated = { ...prev };
        delete updated[nodeId];
        return updated;
      });
      return true;
    } catch (error) {
      console.error('[WebSocket] Failed to delete node parameters:', error);
      return false;
    }
  }, [sendRequest]);

  // =========================================================================
  // Node Execution Operations
  // =========================================================================

  const executeNodeAsync = useCallback(async (
    nodeId: string,
    nodeType: string,
    parameters: Record<string, any>,
    nodes?: any[],
    edges?: any[]
  ): Promise<any> => {
    try {
      // Trigger nodes and long-running agents wait indefinitely - no timeout
      const noTimeout = TRIGGER_NODE_TYPES.includes(nodeType) || LONG_RUNNING_NODE_TYPES.includes(nodeType);
      const timeoutMs = noTimeout ? -1 : undefined;  // -1 = no timeout

      const response = await sendRequest<any>('execute_node', {
        node_id: nodeId,
        node_type: nodeType,
        parameters,
        nodes,
        edges,
        workflow_id: currentWorkflowId  // Include workflow_id for per-workflow status scoping
      }, timeoutMs);
      return response;
    } catch (error) {
      console.error('[WebSocket] Failed to execute node:', error);
      throw error;
    }
  }, [sendRequest, currentWorkflowId]);

  const getNodeOutputAsync = useCallback(async (
    nodeId: string,
    outputName?: string
  ): Promise<any> => {
    try {
      const response = await sendRequest<any>('get_node_output', {
        node_id: nodeId,
        output_name: outputName || 'output_0'
      });
      if (response.success) {
        return response.data;
      }
      return null;
    } catch (error) {
      console.error('[WebSocket] Failed to get node output:', error);
      return null;
    }
  }, [sendRequest]);

  // Cancel event wait (for trigger nodes)
  const cancelEventWaitAsync = useCallback(async (
    nodeId: string,
    waiterId?: string
  ): Promise<{ success: boolean; cancelled_count?: number }> => {
    try {
      const response = await sendRequest<{ success: boolean; cancelled_count?: number }>('cancel_event_wait', {
        node_id: nodeId,
        waiter_id: waiterId
      });
      return response;
    } catch (error) {
      console.error('[WebSocket] Failed to cancel event wait:', error);
      return { success: false };
    }
  }, [sendRequest]);

  const executeWorkflowAsync = useCallback(async (
    nodes: any[],
    edges: any[],
    sessionId?: string
  ): Promise<any> => {
    try {
      const response = await sendRequest<any>('execute_workflow', {
        nodes: nodes.map(node => ({
          id: node.id,
          type: node.type || '',
          data: node.data || {}
        })),
        edges: edges.map(edge => ({
          id: edge.id,
          source: edge.source,
          target: edge.target,
          sourceHandle: edge.sourceHandle || undefined,
          targetHandle: edge.targetHandle || undefined
        })),
        session_id: sessionId || 'default'
      });

      return response;
    } catch (error) {
      console.error('[WebSocket] Failed to execute workflow:', error);
      throw error;
    }
  }, [sendRequest]);

  // =========================================================================
  // Deployment Operations
  // =========================================================================

  const deployWorkflowAsync = useCallback(async (
    workflowId: string,
    nodes: any[],
    edges: any[],
    sessionId?: string
  ): Promise<any> => {
    try {
      const response = await sendRequest<any>('deploy_workflow', {
        workflow_id: workflowId,
        nodes: nodes.map(node => ({
          id: node.id,
          type: node.type || '',
          data: node.data || {}
        })),
        edges: edges.map(edge => ({
          id: edge.id,
          source: edge.source,
          target: edge.target,
          sourceHandle: edge.sourceHandle || undefined,
          targetHandle: edge.targetHandle || undefined
        })),
        session_id: sessionId || 'default'
      });

      return response;
    } catch (error) {
      console.error('[WebSocket] Failed to start deployment:', error);
      throw error;
    }
  }, [sendRequest]);

  const cancelDeploymentAsync = useCallback(async (workflowId?: string): Promise<any> => {
    try {
      const response = await sendRequest<any>('cancel_deployment', {
        workflow_id: workflowId
      });

      // Reset deployment status only if the cancelled workflow matches current
      if (!workflowId || workflowId === deploymentStatus.workflow_id) {
        setDeploymentStatus(defaultDeploymentStatus);
      }

      return response;
    } catch (error) {
      console.error('[WebSocket] Failed to cancel deployment:', error);
      throw error;
    }
  }, [sendRequest, deploymentStatus.workflow_id]);

  const getDeploymentStatusAsync = useCallback(async (workflowId?: string): Promise<{ isRunning: boolean; activeRuns: number; settings?: any; workflow_id?: string }> => {
    try {
      const response = await sendRequest<any>('get_deployment_status', { workflow_id: workflowId });
      return {
        isRunning: response.is_running || false,
        activeRuns: response.active_runs || 0,
        settings: response.settings,
        workflow_id: response.workflow_id
      };
    } catch (error) {
      console.error('[WebSocket] Failed to get deployment status:', error);
      return { isRunning: false, activeRuns: 0 };
    }
  }, [sendRequest]);

  // Cancel any active ad-hoc execution(s) for a workflow.  The backend
  // resets every node currently glowing for this workflow_id and clears
  // its active-run counter.  Distinct from cancelDeployment, which only
  // touches deployed workflows.
  const cancelExecutionAsync = useCallback(async (workflowId: string, nodeId?: string): Promise<any> => {
    try {
      const response = await sendRequest<any>('cancel_execution', {
        workflow_id: workflowId,
        node_id: nodeId,
      });
      // Optimistic local clear so the UI reflects the action immediately.
      const { setWorkflowExecuting } = useAppStore.getState();
      setWorkflowExecuting(workflowId, false);
      return response;
    } catch (error) {
      console.error('[WebSocket] Failed to cancel execution:', error);
      throw error;
    }
  }, [sendRequest]);

  // Snapshot per-workflow execution status from the backend's active-run
  // counter cache.  Called on reconnect / workflow switch so the toolbar
  // button reflects current truth even if we missed broadcasts.
  const getWorkflowStatusAsync = useCallback(async (workflowId: string): Promise<{ executing: boolean }> => {
    try {
      const response = await sendRequest<any>('get_workflow_status', { workflow_id: workflowId });
      const data = response?.data || {};
      return { executing: !!data.executing };
    } catch (error) {
      console.error('[WebSocket] Failed to get workflow status:', error);
      return { executing: false };
    }
  }, [sendRequest]);

  // =========================================================================
  // AI Operations
  // =========================================================================

  const executeAiNodeAsync = useCallback(async (
    nodeId: string,
    nodeType: string,
    parameters: Record<string, any>,
    model: string,
    workflowId: string,
    nodes: any[],
    edges: any[]
  ): Promise<any> => {
    try {
      // AI nodes can run for minutes (tool calling, RLM iterations) - no timeout
      const response = await sendRequest<any>('execute_ai_node', {
        node_id: nodeId,
        node_type: nodeType,
        parameters,
        model,
        workflow_id: workflowId,
        nodes,
        edges
      }, -1);
      return response;
    } catch (error) {
      console.error('[WebSocket] Failed to execute AI node:', error);
      throw error;
    }
  }, [sendRequest]);

  const getAiModelsAsync = useCallback(async (provider: string, apiKey: string): Promise<string[]> => {
    try {
      const response = await sendRequest<any>('get_ai_models', {
        provider,
        api_key: apiKey
      });
      return response.models || [];
    } catch (error) {
      console.error('[WebSocket] Failed to get AI models:', error);
      return [];
    }
  }, [sendRequest]);

  // =========================================================================
  // API Key Operations
  // =========================================================================

  const validateApiKeyAsync = useCallback(async (
    provider: string,
    apiKey: string
  ): Promise<{ valid: boolean; message?: string; models?: string[] }> => {
    try {
      const response = await sendRequest<any>('validate_api_key', {
        provider,
        api_key: apiKey
      });
      // Backend returns one of:
      //   { success: true,  valid: true,  models }         — key is good
      //   { success: true,  valid: false, message }        — clean rejection (401/403/timeout/etc)
      //   { success: false, error }                        — handler bug (uncaught exception)
      // Surface the message field first; fall back to error so the toast
      // shows the actual reason instead of a generic "Validation failed".
      const result = {
        valid: response.valid === true,
        message: response.message ?? response.error,
        models: response.models
      };

      // Update apiKeyStatuses on successful validation. "is stored"
      // lives on catalogue.provider.stored — don't duplicate it here.
      if (result.valid) {
        setApiKeyStatuses(prev => ({
          ...prev,
          [provider]: { valid: true, models: result.models }
        }));
      }

      return result;
    } catch (error) {
      console.error('[WebSocket] Failed to validate API key:', error);
      return { valid: false, message: 'Validation failed' };
    }
  }, [sendRequest]);

  const getStoredApiKeyAsync = useCallback(async (
    provider: string
  ): Promise<{ hasKey: boolean; apiKey?: string; models?: string[] }> => {
    try {
      // Backend emits camelCase (hasKey/apiKey) — same convention as the
      // update_api_key_status broadcast. No per-field adapter needed.
      const response = await sendRequest<{
        hasKey?: boolean;
        apiKey?: string;
        models?: string[];
      }>('get_stored_api_key', { provider });
      const result = {
        hasKey: !!response.hasKey,
        apiKey: response.apiKey,
        models: response.models,
      };

      // Mirror models into apiKeyStatuses so consumers reading from
      // context stay in sync without an extra round-trip. "is stored"
      // lives on the catalogue's provider.stored, not here.
      if (result.hasKey) {
        setApiKeyStatuses(prev => ({
          ...prev,
          [provider]: { valid: true, models: result.models }
        }));
      }

      return result;
    } catch (error) {
      console.error('[WebSocket] Failed to get stored API key:', error);
      return { hasKey: false };
    }
  }, [sendRequest]);

  const saveApiKeyAsync = useCallback(async (
    provider: string,
    apiKey: string,
    models?: string[]
  ): Promise<boolean> => {
    try {
      const response = await sendRequest<any>('save_api_key', {
        provider,
        api_key: apiKey,
        models
      });
      const success = response.success !== false;

      // Update apiKeyStatuses on successful save. The 'valid: true'
      // here is optimistic — save_api_key doesn't actually validate
      // upstream. Catalogue refetch (via the credential.api_key.saved
      // broadcast) is the truthful source for "is stored".
      if (success) {
        setApiKeyStatuses(prev => ({
          ...prev,
          [provider]: { valid: true, models }
        }));
      }

      return success;
    } catch (error) {
      console.error('[WebSocket] Failed to save API key:', error);
      return false;
    }
  }, [sendRequest]);

  const deleteApiKeyAsync = useCallback(async (provider: string): Promise<boolean> => {
    try {
      await sendRequest<any>('delete_api_key', { provider });
      // Don't optimistically clear apiKeyStatuses[provider] here. The
      // backend's `api_key_status` broadcast (fired before this reply
      // lands) already wrote `{valid: false, hasKey: false, message:
      // 'deleted'}` to every connected client. The catalogue refetch
      // (debounced 300 ms after `credential_catalogue_updated`) will
      // flip `provider.stored` to false. A local optimistic clear here
      // raced with the broadcast and produced a green-flash mid-delete:
      // broadcast → red, optimistic clear → green (validation undefined
      // but stored still cached true), catalogue refetch → gray.
      // Trusting the broadcast pipeline gives a clean red → gray.
      return true;
    } catch (error) {
      console.error('[WebSocket] Failed to delete API key:', error);
      return false;
    }
  }, [sendRequest]);

  // =========================================================================
  // Android Operations
  // =========================================================================

  const getAndroidDevicesAsync = useCallback(async (): Promise<string[]> => {
    try {
      const response = await sendRequest<any>('get_android_devices', {});
      return response.devices || [];
    } catch (error) {
      console.error('[WebSocket] Failed to get Android devices:', error);
      return [];
    }
  }, [sendRequest]);

  const executeAndroidActionAsync = useCallback(async (
    serviceId: string,
    action: string,
    parameters: Record<string, any>,
    deviceId?: string
  ): Promise<any> => {
    try {
      const response = await sendRequest<any>('execute_android_action', {
        service_id: serviceId,
        action,
        parameters,
        device_id: deviceId
      });
      return response;
    } catch (error) {
      console.error('[WebSocket] Failed to execute Android action:', error);
      throw error;
    }
  }, [sendRequest]);

  // =========================================================================
  // Maps Operations
  // =========================================================================

  const validateMapsKeyAsync = useCallback(async (
    apiKey: string
  ): Promise<{ valid: boolean; message?: string }> => {
    try {
      const response = await sendRequest<any>('validate_maps_key', { api_key: apiKey });
      return {
        valid: response.valid || false,
        message: response.message
      };
    } catch (error) {
      console.error('[WebSocket] Failed to validate Maps key:', error);
      return { valid: false, message: 'Validation failed' };
    }
  }, [sendRequest]);

  // =========================================================================
  // Apify Operations
  // =========================================================================

  const validateApifyKeyAsync = useCallback(async (
    apiKey: string
  ): Promise<{ valid: boolean; message?: string; username?: string }> => {
    try {
      const response = await sendRequest<any>('validate_apify_key', { api_key: apiKey });
      return {
        valid: response.valid || false,
        message: response.message,
        username: response.username
      };
    } catch (error) {
      console.error('[WebSocket] Failed to validate Apify key:', error);
      return { valid: false, message: 'Validation failed' };
    }
  }, [sendRequest]);

  // =========================================================================
  // WhatsApp Operations
  // =========================================================================

  const getWhatsAppStatusAsync = useCallback(async (): Promise<{ connected: boolean; deviceId?: string; data?: any }> => {
    try {
      const response = await sendRequest<any>('whatsapp_status', {});
      return {
        connected: response.connected || false,
        deviceId: response.device_id,
        data: response.data
      };
    } catch (error) {
      console.error('[WebSocket] Failed to get WhatsApp status:', error);
      return { connected: false };
    }
  }, [sendRequest]);

  const getWhatsAppQRAsync = useCallback(async (): Promise<{ connected: boolean; qr?: string; message?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_qr', {});
      return {
        connected: response.connected || false,
        qr: response.qr,
        message: response.message
      };
    } catch (error) {
      console.error('[WebSocket] Failed to get WhatsApp QR:', error);
      return { connected: false, message: 'Failed to get QR code' };
    }
  }, [sendRequest]);

  const sendWhatsAppMessageAsync = useCallback(async (
    phone: string,
    message: string
  ): Promise<{ success: boolean; messageId?: string; error?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_send', { phone, message });
      return {
        success: response.success || false,
        messageId: response.messageId,
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to send WhatsApp message:', error);
      return { success: false, error: error.message || 'Send failed' };
    }
  }, [sendRequest]);

  const startWhatsAppConnectionAsync = useCallback(async (): Promise<{ success: boolean; message?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_start', {});
      return {
        success: response.success !== false,
        message: response.message
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to start WhatsApp connection:', error);
      return { success: false, message: error.message || 'Failed to start' };
    }
  }, [sendRequest]);

  const restartWhatsAppConnectionAsync = useCallback(async (): Promise<{ success: boolean; message?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_restart', {});
      return {
        success: response.success !== false,
        message: response.message
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to restart WhatsApp connection:', error);
      return { success: false, message: error.message || 'Failed to restart' };
    }
  }, [sendRequest]);

  const getWhatsAppGroupsAsync = useCallback(async (): Promise<{ success: boolean; groups: Array<{ jid: string; name: string; topic?: string; size?: number; is_community?: boolean }>; error?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_groups', {});
      return {
        success: response.success !== false,
        groups: response.groups || [],
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to get WhatsApp groups:', error);
      return { success: false, groups: [], error: error.message || 'Failed to get groups' };
    }
  }, [sendRequest]);

  const getWhatsAppChannelsAsync = useCallback(async (): Promise<{ success: boolean; channels: Array<{ jid: string; name: string; subscriber_count?: number; role?: string }>; error?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_newsletters', {});
      const channels = response.channels || response.result?.channels || [];
      return {
        success: response.success !== false,
        channels,
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to get WhatsApp channels:', error);
      return { success: false, channels: [], error: error.message || 'Failed to get channels' };
    }
  }, [sendRequest]);

  const getWhatsAppGroupInfoAsync = useCallback(async (groupId: string): Promise<{ success: boolean; participants: Array<{ phone: string; name: string; jid: string; is_admin?: boolean }>; name?: string; error?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_group_info', { group_id: groupId });
      return {
        success: response.success !== false,
        participants: response.participants || [],
        name: response.name,
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to get WhatsApp group info:', error);
      return { success: false, participants: [], error: error.message || 'Failed to get group info' };
    }
  }, [sendRequest]);

  const getWhatsAppRateLimitConfigAsync = useCallback(async (): Promise<{ success: boolean; config?: RateLimitConfig; stats?: RateLimitStats; error?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_rate_limit_get', {});
      return {
        success: response.success !== false,
        config: response.config,
        stats: response.stats,
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to get WhatsApp rate limit config:', error);
      return { success: false, error: error.message || 'Failed to get rate limit config' };
    }
  }, [sendRequest]);

  const setWhatsAppRateLimitConfigAsync = useCallback(async (config: Partial<RateLimitConfig>): Promise<{ success: boolean; config?: RateLimitConfig; error?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_rate_limit_set', { config });
      return {
        success: response.success !== false,
        config: response.config,
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to set WhatsApp rate limit config:', error);
      return { success: false, error: error.message || 'Failed to set rate limit config' };
    }
  }, [sendRequest]);

  const getWhatsAppRateLimitStatsAsync = useCallback(async (): Promise<{ success: boolean; stats?: RateLimitStats; error?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_rate_limit_stats', {});
      return {
        success: response.success !== false,
        stats: response.stats || response,
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to get WhatsApp rate limit stats:', error);
      return { success: false, error: error.message || 'Failed to get rate limit stats' };
    }
  }, [sendRequest]);

  const unpauseWhatsAppRateLimitAsync = useCallback(async (): Promise<{ success: boolean; stats?: RateLimitStats; error?: string }> => {
    try {
      const response = await sendRequest<any>('whatsapp_rate_limit_unpause', {});
      return {
        success: response.success !== false,
        stats: response.stats,
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to unpause WhatsApp rate limit:', error);
      return { success: false, error: error.message || 'Failed to unpause rate limit' };
    }
  }, [sendRequest]);

  // =========================================================================
  // Memory and Skill Operations
  // =========================================================================

  const clearMemoryAsync = useCallback(async (
    sessionId: string,
    clearLongTerm = false,
    workflowId?: string
  ): Promise<{ success: boolean; default_content?: string; cleared_vector_store?: boolean; cleared_todo_keys?: string[]; error?: string }> => {
    try {
      const response = await sendRequest<any>('clear_memory', {
        session_id: sessionId,
        clear_long_term: clearLongTerm,
        workflow_id: workflowId,
      });
      return {
        success: response.success !== false,
        default_content: response.default_content,
        cleared_vector_store: response.cleared_vector_store,
        cleared_todo_keys: response.cleared_todo_keys,
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to clear memory:', error);
      return { success: false, error: error.message || 'Failed to clear memory' };
    }
  }, [sendRequest]);

  const resetSkillAsync = useCallback(async (
    skillName: string
  ): Promise<{ success: boolean; original_content?: string; is_builtin?: boolean; error?: string }> => {
    try {
      const response = await sendRequest<any>('reset_skill', {
        skill_name: skillName
      });
      return {
        success: response.success !== false,
        original_content: response.original_content,
        is_builtin: response.is_builtin,
        error: response.error
      };
    } catch (error: any) {
      console.error('[WebSocket] Failed to reset skill:', error);
      return { success: false, error: error.message || 'Failed to reset skill' };
    }
  }, [sendRequest]);

  // Track if component is mounted to prevent state updates after unmount
  const isMountedRef = useRef(true);

  // Connect only when authenticated (not during auth loading)
  useEffect(() => {
    isMountedRef.current = true;

    // Don't connect if still loading auth or not authenticated
    if (authLoading || !isAuthenticated) {
      return;
    }

    // Skip if already connected
    if (wsRef.current?.readyState === ReconnectingWebSocket.OPEN) {
      return;
    }

    // Small delay to avoid React Strict Mode double-connection issues
    const connectTimeout = setTimeout(() => {
      if (isMountedRef.current && isAuthenticated && !wsRef.current) {
        connect();
      }
    }, 100);

    return () => {
      clearTimeout(connectTimeout);
    };
  }, [connect, isAuthenticated, authLoading]);

  // Handle logout - separate effect to avoid reconnect loops
  useEffect(() => {
    if (!isAuthenticated && wsRef.current) {
      // Drain the pending-send queue with a clear auth error before closing.
      if (pendingSendQueueRef.current.length > 0) {
        for (const queued of pendingSendQueueRef.current) {
          try {
            queued.abortController.abort();
            queued.reject(new Error('auth: not authenticated'));
          } catch {
            // Ignore — caller may have already settled.
          }
        }
        pendingSendQueueRef.current = [];
      }
      wsRef.current.close(WS_CLOSE.NORMAL_CLOSURE, 'User logged out');
      wsRef.current = null;
      setIsConnected(false);
      setIsReady(false);
    }
  }, [isAuthenticated]);

  // Cleanup on unmount only
  useEffect(() => {
    return () => {
      isMountedRef.current = false;
      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
      // Drain the pending-send queue so any in-flight awaiters fail fast on
      // unmount instead of dangling forever.
      if (pendingSendQueueRef.current.length > 0) {
        for (const queued of pendingSendQueueRef.current) {
          try {
            queued.abortController.abort();
            queued.reject(new Error('WebSocket unmounting'));
          } catch {
            // Ignore — caller may have already settled.
          }
        }
        pendingSendQueueRef.current = [];
      }
      if (wsRef.current?.readyState === ReconnectingWebSocket.OPEN) {
        wsRef.current.close(WS_CLOSE.NORMAL_CLOSURE, 'Component unmounted');
      }
    };
  }, []);

  // Memoized provider value: spreading a fresh object each render forces
  // every useContext consumer to re-render on any state change. Wrapping
  // in useMemo with the actual state deps keeps consumers stable when
  // unrelated slices change. Async methods are useCallback-wrapped
  // upstream, so their identities are stable across renders.
  // Reference: https://overreacted.io/before-you-memo/
  const value: WebSocketContextValue = useMemo(() => ({
    // Connection state
    isConnected,
    isReady,
    reconnecting,

    // Status data
    androidStatus,
    setAndroidStatus,
    whatsappStatus,
    twitterStatus,
    googleStatus,
    telegramStatus,
    whatsappMessages,
    lastWhatsAppMessage,
    apiKeyStatuses,
    consoleLogs,
    terminalLogs,
    chatMessages,
    nodeStatuses,
    nodeParameters,
    variables,
    workflowStatus,
    deploymentStatus,
    workflowLock,

    // Compaction stats (real-time via broadcasts)
    compactionStats,
    updateCompactionStats,

    // Status getters
    getNodeStatus,
    getApiKeyStatus,
    getVariable,
    requestStatus,
    clearNodeStatus,
    clearWhatsAppMessages,
    clearConsoleLogs,
    clearTerminalLogs,
    clearChatMessages,
    sendChatMessage: sendChatMessageAsync,

    // Generic request method
    sendRequest,

    // Generic broadcast subscription
    addEventListener: (type: string, handler: (data: any) => void) => {
      let set = eventListenersRef.current.get(type);
      if (!set) {
        set = new Set();
        eventListenersRef.current.set(type, set);
      }
      set.add(handler);
      return () => {
        const current = eventListenersRef.current.get(type);
        if (current) {
          current.delete(handler);
          if (current.size === 0) eventListenersRef.current.delete(type);
        }
      };
    },

    // Node Parameters
    getNodeParameters: getNodeParametersAsync,
    getAllNodeParameters: getAllNodeParametersAsync,
    saveNodeParameters: saveNodeParametersAsync,
    deleteNodeParameters: deleteNodeParametersAsync,

    // Node Execution
    executeNode: executeNodeAsync,
    executeWorkflow: executeWorkflowAsync,
    getNodeOutput: getNodeOutputAsync,

    // Trigger/Event Waiting
    cancelEventWait: cancelEventWaitAsync,

    // Deployment Operations
    deployWorkflow: deployWorkflowAsync,
    cancelDeployment: cancelDeploymentAsync,
    getDeploymentStatus: getDeploymentStatusAsync,
    cancelExecution: cancelExecutionAsync,
    getWorkflowStatus: getWorkflowStatusAsync,

    // AI Operations
    executeAiNode: executeAiNodeAsync,
    getAiModels: getAiModelsAsync,

    // API Key Operations
    validateApiKey: validateApiKeyAsync,
    getStoredApiKey: getStoredApiKeyAsync,
    saveApiKey: saveApiKeyAsync,
    deleteApiKey: deleteApiKeyAsync,

    // Android Operations
    getAndroidDevices: getAndroidDevicesAsync,
    executeAndroidAction: executeAndroidActionAsync,
    // Maps Operations
    validateMapsKey: validateMapsKeyAsync,

    // Apify Operations
    validateApifyKey: validateApifyKeyAsync,

    // WhatsApp Operations
    getWhatsAppStatus: getWhatsAppStatusAsync,
    getWhatsAppQR: getWhatsAppQRAsync,
    sendWhatsAppMessage: sendWhatsAppMessageAsync,
    startWhatsAppConnection: startWhatsAppConnectionAsync,
    restartWhatsAppConnection: restartWhatsAppConnectionAsync,
    getWhatsAppGroups: getWhatsAppGroupsAsync,
    getWhatsAppChannels: getWhatsAppChannelsAsync,
    getWhatsAppGroupInfo: getWhatsAppGroupInfoAsync,
    getWhatsAppRateLimitConfig: getWhatsAppRateLimitConfigAsync,
    setWhatsAppRateLimitConfig: setWhatsAppRateLimitConfigAsync,
    getWhatsAppRateLimitStats: getWhatsAppRateLimitStatsAsync,
    unpauseWhatsAppRateLimit: unpauseWhatsAppRateLimitAsync,

    // Memory and Skill Operations
    clearMemory: clearMemoryAsync,
    resetSkill: resetSkillAsync,
  }), [
    isConnected, isReady, reconnecting,
    androidStatus, setAndroidStatus,
    whatsappStatus, twitterStatus, googleStatus, telegramStatus,
    whatsappMessages, lastWhatsAppMessage,
    apiKeyStatuses,
    consoleLogs, terminalLogs, chatMessages,
    nodeStatuses, nodeParameters,
    variables, workflowStatus, deploymentStatus, workflowLock,
    compactionStats, updateCompactionStats,
    getNodeStatus, getApiKeyStatus, getVariable,
    requestStatus, clearNodeStatus,
    clearWhatsAppMessages, clearConsoleLogs, clearTerminalLogs, clearChatMessages,
    sendChatMessageAsync, sendRequest,
    getNodeParametersAsync, getAllNodeParametersAsync,
    saveNodeParametersAsync, deleteNodeParametersAsync,
    executeNodeAsync, executeWorkflowAsync, getNodeOutputAsync,
    cancelEventWaitAsync,
    deployWorkflowAsync, cancelDeploymentAsync, getDeploymentStatusAsync,
    cancelExecutionAsync, getWorkflowStatusAsync,
    executeAiNodeAsync, getAiModelsAsync,
    validateApiKeyAsync, getStoredApiKeyAsync, saveApiKeyAsync, deleteApiKeyAsync,
    getAndroidDevicesAsync, executeAndroidActionAsync,
    validateMapsKeyAsync, validateApifyKeyAsync,
    getWhatsAppStatusAsync, getWhatsAppQRAsync, sendWhatsAppMessageAsync,
    startWhatsAppConnectionAsync, restartWhatsAppConnectionAsync,
    getWhatsAppGroupsAsync, getWhatsAppChannelsAsync, getWhatsAppGroupInfoAsync,
    getWhatsAppRateLimitConfigAsync, setWhatsAppRateLimitConfigAsync,
    getWhatsAppRateLimitStatsAsync, unpauseWhatsAppRateLimitAsync,
    clearMemoryAsync, resetSkillAsync,
  ]);

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  );
};

// Hook to use WebSocket context
export const useWebSocket = (): WebSocketContextValue => {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocket must be used within a WebSocketProvider');
  }
  return context;
};

// Hook specifically for Android status
export const useAndroidStatus = (): AndroidStatus & { isConnected: boolean } => {
  const { androidStatus, isConnected } = useWebSocket();
  return {
    ...androidStatus,
    isConnected
  };
};

// Hook specifically for node status. Subscribes directly to the
// node-status Zustand store so a single node's consumers re-render
// only when *that node's* slot changes — not on every status tick
// for every other node on the canvas.
export const useNodeStatus = (nodeId: string): NodeStatus | undefined => {
  return useNodeStatusForId(nodeId);
};

// Hook specifically for workflow status
export const useWorkflowStatus = (): WorkflowStatus => {
  const { workflowStatus } = useWebSocket();
  return workflowStatus;
};

// Hook specifically for API key status
export const useApiKeyStatus = (provider: string): ApiKeyStatus | undefined => {
  const { getApiKeyStatus } = useWebSocket();
  return getApiKeyStatus(provider);
};

// Hook specifically for WhatsApp status
export const useWhatsAppStatus = (): WhatsAppStatus => {
  const { whatsappStatus } = useWebSocket();
  return whatsappStatus;
};

// Hook specifically for Twitter status
export const useTwitterStatus = (): TwitterStatus => {
  const { twitterStatus } = useWebSocket();
  return twitterStatus;
};

// Hook specifically for Google Workspace status
export const useGoogleStatus = (): GoogleStatus => {
  const { googleStatus } = useWebSocket();
  return googleStatus;
};

// Hook specifically for Telegram status
export const useTelegramStatus = (): TelegramStatus => {
  const { telegramStatus } = useWebSocket();
  return telegramStatus;
};

// Hook specifically for deployment status
export const useDeploymentStatus = (): DeploymentStatus => {
  const { deploymentStatus } = useWebSocket();
  return deploymentStatus;
};

// Hook specifically for workflow lock status
export const useWorkflowLock = (): WorkflowLock => {
  const { workflowLock } = useWebSocket();
  return workflowLock;
};

// Hook specifically for WhatsApp messages (for trigger nodes)
export const useWhatsAppMessages = (): {
  messages: WhatsAppMessage[];
  lastMessage: WhatsAppMessage | null;
  clearMessages: () => void;
} => {
  const { whatsappMessages, lastWhatsAppMessage, clearWhatsAppMessages } = useWebSocket();
  return {
    messages: whatsappMessages,
    lastMessage: lastWhatsAppMessage,
    clearMessages: clearWhatsAppMessages
  };
};

// Hook to check if a tool is currently being executed by any AI Agent
// Used by tool nodes to show spinning indicator when they're being used
export const useIsToolExecuting = (toolName: string): boolean => {
  const { nodeStatuses } = useWebSocket();

  // Debug: Log what we're checking
  if (toolName) {
    const statusCount = Object.keys(nodeStatuses).length;
    if (statusCount > 0) {
      console.log(`[useIsToolExecuting] Checking for tool '${toolName}', nodeStatuses count:`, statusCount, nodeStatuses);
    }
  }

  // Scan all node statuses to find if any AI Agent is executing this tool
  // The status object contains phase and tool_name directly (not nested under data)
  for (const nodeId in nodeStatuses) {
    const status = nodeStatuses[nodeId] as Record<string, any>;
    if (status?.phase === 'executing_tool') {
      console.log(`[useIsToolExecuting] Found executing_tool phase for node ${nodeId}:`, status);
      if (status?.tool_name === toolName) {
        console.log(`[useIsToolExecuting] MATCH! Tool '${toolName}' is executing`);
        return true;
      }
    }
  }
  return false;
};

export default WebSocketContext;
