// Modern n8n-inspired node property system
// Clean implementation with n8n-compatible data flow

// ============================================================================
// DATA FLOW INTERFACES - n8n-compatible data passing between nodes
// ============================================================================

// Binary data interface for files, images, documents, etc.
export interface IBinaryData {
  mimeType: string;
  fileName?: string;
  directory?: string;
  fileExtension?: string;
  fileSize?: number;
  data: string; // Base64 encoded data
  id?: string;
}

// Core data structure that flows between nodes (n8n-compatible)
export interface INodeExecutionData {
  json: Record<string, any>; // Main JSON data
  binary?: Record<string, IBinaryData>; // Binary attachments (files, images, etc.)
  pairedItem?: {
    item: number;
    input?: number;
  };
  error?: Error;
}

// Input/Output connection types
export type NodeConnectionType = 
  | 'main'      // Standard data connection
  | 'trigger'   // Trigger/start connection
  | 'ai'        // AI service responses
  | 'file'      // File data
  | 'binary'    // Binary data specific
  | 'webhook'   // Webhook data
  | 'context'   // Durable agent context
  | 'memory'    // Explicit memory tool
  | 'tool'
  | 'skill'
  | 'task'
  | 'teammates';

/** Lossless mirror of one backend NodeSpec handle.
 *
 * `inputs` / `outputs` remain available for legacy consumers, while this
 * field preserves the React Flow handle id, placement, label, offset and
 * semantic role declared by the backend.
 */
export interface INodeHandleDescription {
  name: string;
  kind: 'input' | 'output';
  position: 'top' | 'bottom' | 'left' | 'right';
  offset?: string;
  label?: string;
  role?: string;
}

// Enhanced output definition with data structure info
export interface INodeOutputDefinition {
  name: string;
  displayName: string;
  type: NodeConnectionType;
  description: string;
  maxConnections?: number; // How many nodes can connect to this output
  dataStructure?: {
    // Expected JSON structure this output provides
    properties: Record<string, {
      type: 'string' | 'number' | 'boolean' | 'object' | 'array';
      description?: string;
      required?: boolean;
    }>;
    binaryData?: boolean; // Whether this output includes binary data
  };
}

// Enhanced input definition  
export interface INodeInputDefinition {
  name: string;
  displayName: string;
  type: NodeConnectionType;
  description: string;
  required?: boolean;
  maxConnections?: number; // How many connections this input accepts
  acceptedDataTypes?: string[]; // What kind of data this input can handle
}

// ============================================================================
// PROPERTY DEFINITION INTERFACES
// ============================================================================

export interface INodePropertyOption {
  name: string;
  value: string | number | boolean;
  label?: string;
  description?: string;
  action?: string;
}

export interface INodePropertyCollection {
  displayName: string;
  name: string;
  values: INodeProperties[];
}

export interface INodePropertyTypeOptions {
  loadOptionsMethod?: string;
  loadOptionsDependsOn?: string[];
  multipleValues?: boolean;
  multipleValueButtonText?: string;
  maxValue?: number;
  minValue?: number;
  numberStepSize?: number;
  password?: boolean;
  rows?: number;
  editor?: 'code' | 'json' | 'html' | 'sql';
  editorLanguage?: string;
  // Dynamic parameter options
  dynamicOptions?: boolean;
  dependsOn?: string[];
  // File input options
  accept?: string; // MIME types or file extensions (e.g., 'image/*', '.pdf,.doc')
}

export interface INodePropertyDisplayOptions {
  show?: Record<string, any[]>;
  hide?: Record<string, any[]>;
}

export interface INodePropertyValidation {
  type?: 'regex' | 'email' | 'url' | 'json' | 'apiKey';
  pattern?: string;
  message?: string;
  // API Key validation specific properties
  provider?: string;
  showValidateButton?: boolean;
}

// Core property interface - modern n8n-inspired design
export interface INodeProperties {
  displayName: string;
  name: string;
  type:
    | 'string'
    | 'number'
    | 'boolean'
    | 'options'
    | 'multiOptions'
    | 'collection'
    | 'fixedCollection'
    | 'color'
    | 'dateTime'
    | 'json'
    | 'notice'
    | 'hidden'
    | 'resourceLocator'
    | 'code'
    | 'file';
  
  default?: any;
  description?: string;
  placeholder?: string;
  required?: boolean;
  noDataExpression?: boolean;
  
  /**
   * Enum choices (``type: "options"`` / ``"multiOptions"``) OR nested
   * child properties for a ``type: "collection"`` container. The
   * collection case lets the adapter nest grouped fields under a
   * collapsible parent (see ``nodeSpecToDescription.groupProperties``).
   */
  options?: INodePropertyOption[] | INodePropertyCollection[] | INodeProperties[];
  typeOptions?: INodePropertyTypeOptions;
  displayOptions?: INodePropertyDisplayOptions;
  validation?: INodePropertyValidation[];
}

export interface INodeCredentialDescription {
  name: string;
  required?: boolean;
  displayName?: string;
}

// Resource definition for Resource-Operation pattern
export interface INodeResourceDefinition {
  name: string;
  displayName: string;
  icon?: string;
  description?: string;
  operations: INodeOperationDefinition[];
}

export interface INodeOperationDefinition {
  name: string;
  displayName: string;
  description?: string;
  action?: string;
  properties: INodeProperties[];
}


// Main node type description interface
/**
 * Per-node-definition UI hints. Lets panels and the inspector make
 * rendering decisions from the schema instead of `nodeDefinition.name === '…'`
 * string compares scattered across the UI tree. Each flag is consumed by
 * exactly one panel; defaults to `false` (the panel renders normally).
 */
export interface INodeUIHints {
  /** Graph capability: this node must own one system-managed Context node. */
  requiresContext?: boolean;
  /** The graph lifecycle owns this node; it is not directly palette-created. */
  systemManaged?: boolean;
  /** MiddleSection: render the authorized Context inspector. */
  isContextPanel?: boolean;
  /** MiddleSection: render explicit durable Memory item controls. */
  isMemoryToolPanel?: boolean;
  /** MiddleSection: render the Data node's mounts + read-only browser. */
  isDataPanel?: boolean;
  /** ParameterPanel: skip the Input section (e.g. start, skill, monitor). */
  hideInputSection?: boolean;
  /** ParameterPanel: skip the Output section (e.g. start, skill). */
  hideOutputSection?: boolean;
  /** ParameterPanel: hide the Run button (e.g. skill / memory / tool nodes). */
  hideRunButton?: boolean;
  /** MiddleSection: give the params block extra flex space for an embedded code editor. */
  hasCodeEditor?: boolean;
  /** MiddleSection: render the MasterSkillEditor split panel instead of the plain params list. */
  isMasterSkillEditor?: boolean;
  /** MiddleSection: render the memory markdown panel + token usage stats. */
  isMemoryPanel?: boolean;
  /** MiddleSection: render the editable Current Todos manager (writeTodos)
   * instead of the plain params list. */
  isTodoEditor?: boolean;
  /** MiddleSection: render the team-monitor panel. */
  isMonitorPanel?: boolean;
  /** MiddleSection: render the execution-scoped team task control panel. */
  isTaskManagerPanel?: boolean;
  /** MiddleSection: render live managed-process inspection and controls. */
  isProcessManagerPanel?: boolean;
  /** MiddleSection: render the workspace gallery (drag files to params). */
  isGalleryPanel?: boolean;
  /** MiddleSection: render the pushed-content Canvas board. The docked
   * canvas sidebar reads the same flag to find Canvas nodes. */
  isCanvasPanel?: boolean;
  /** Live browser view backed by the plugin's browser-session transport. */
  isBrowserPanel?: boolean;
  /** Special-case panel for gmaps_create with map preview. */
  showLocationPanel?: boolean;
  /** ConsolePanel: this node is a chat-message target. */
  isChatTrigger?: boolean;
  /** ConsolePanel: this node consumes console output (filter source). */
  isConsoleSink?: boolean;
  /** Agent panels show the connected-skills section. */
  hasSkills?: boolean;
  /** InputSection / OutputPanel: this node is auxiliary configuration
   * (memory, tool). Its panel inherits the parent's main inputs
   * instead of showing direct upstream connections. Auto-derived on
   * the backend from group membership; plugins can override. */
  isConfigNode?: boolean;
  /** OutputPanel: how the node's textual output renders. `"terminal"`
   * = preformatted CLI text (never markdown — `#` would become
   * headings and indentation would collapse). Declared by CLI-wrapper
   * plugins (githubAction, vercelAction, shell). `"audio"` = render an
   * `<audio>` player for a result carrying an AudioRef (`kind:
   * "audio"`), declared by textToSpeech; without it the ref falls
   * through to the JSON tree. Widening this union needs no change to
   * the backend's `test_ui_hints_only_carry_known_flags` — that
   * invariant checks flag *names*, not values. */
  outputMode?: 'terminal' | 'audio';
  /** How long this node may legitimately run, in milliseconds — the backend's
   * own `BaseNode.start_to_close_timeout`, serialized. The WebSocket layer
   * sizes its request budget from this instead of keeping a list of "slow"
   * node types, which had drifted to 5-of-10 triggers and 16-of-21 agents.
   *
   * Read it off the RAW spec (`getCachedNodeSpec`), never through
   * `resolveNodeDescription` — the adapter drops uiHints' siblings and this
   * value must match what the server will actually allow. */
  executionTimeoutMs?: number;
  /** Canvas node box size in px, declared by the plugin (agents via
   * `STD_SIZE`, social send/receive). `AIAgentNode` reads them; absent
   * hints fall back to 300x200. */
  width?: number;
  height?: number;
  /** InputSection: the start node's user-authored JSON blob is the
   * upstream "output" to show (Wave 10.G.5). */
  hasInitialDataBlob?: boolean;
}

export interface INodeTypeDescription {
  displayName: string;
  name: string;
  // Wave 10.B: icon comes from the backend NodeSpec; local
  // nodeDefinitions/*.ts no longer declare icons.
  icon?: string;
  group: string[];
  version: number;
  subtitle?: string;
  description: string;
  keywords?: string[];
  defaults: {
    name: string;
    color?: string;
  };
  inputs: string[] | INodeInputDefinition[];
  outputs: string[] | INodeOutputDefinition[];
  /** Exact backend-declared handle topology. */
  handles?: INodeHandleDescription[];
  properties: INodeProperties[];
  credentials?: INodeCredentialDescription[];
  resources?: INodeResourceDefinition[];
  methods?: {
    loadOptions?: Record<string, (this: any) => Promise<INodePropertyOption[]>>;
  };
  /** Per-node UI hints; see INodeUIHints for each flag. */
  uiHints?: INodeUIHints;
  // NOTE: runtime output shape is no longer declared on the frontend. It is
  // served lazy by the backend at GET /api/schemas/nodes/{nodeType}.json and
  // consumed by InputSection via useNodeOutputSchemaQuery. See
  // docs-internal/ARCHIVE/schema_source_of_truth_rfc.md.
}

// Node type interface that nodes must implement
export interface INodeType {
  description: INodeTypeDescription;
  methods?: {
    loadOptions?: Record<string, () => Promise<INodePropertyOption[]>>;
  };
  execute?(inputData: INodeExecutionData[][]): Promise<INodeExecutionData[][]>;
}

// Execution context for nodes
export interface IExecuteContext {
  nodeId: string;
  nodeType?: string; // Add nodeType to context
  parameters: Record<string, any>;
  inputData: INodeExecutionData[][];
  workflow: {
    id: string;
    name: string;
  };
  connectionType?: NodeConnectionType;
}
