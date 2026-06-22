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
  | 'webhook';  // Webhook data

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
  /** MiddleSection: surface the ToolSchemaEditor for connected services. */
  isToolPanel?: boolean;
  /** MiddleSection: render the editable Current Todos manager (writeTodos)
   * instead of the plain params list. */
  isTodoEditor?: boolean;
  /** MiddleSection: render the team-monitor panel. */
  isMonitorPanel?: boolean;
  /** Special-case panel for gmaps_create with map preview. */
  showLocationPanel?: boolean;
  /** ToolSchemaEditor visibility (Android toolkit aggregator). */
  isAndroidToolkit?: boolean;
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
  // docs-internal/schema_source_of_truth_rfc.md.
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

// ============================================================================  
// HELPER FUNCTIONS FOR DATA FLOW
// ============================================================================

// Helper to create execution data in n8n format
export function createNodeExecutionData(json: Record<string, any>, binary?: Record<string, IBinaryData>): INodeExecutionData {
  return {
    json,
    binary,
    pairedItem: { item: 0 }
  };
}

// Helper to create simple text output
export function createTextOutput(text: string, additionalData?: Record<string, any>): INodeExecutionData[] {
  return [{
    json: {
      text,
      ...additionalData
    }
  }];
}

// Helper to create file output
export function createFileOutput(filePath: string, fileName: string, mimeType: string, additionalData?: Record<string, any>): INodeExecutionData[] {
  return [{
    json: {
      fileName,
      filePath,
      mimeType,
      ...additionalData
    },
    binary: {
      file: {
        data: '', // Would be populated with actual file data
        fileName,
        mimeType,
        fileSize: 0
      }
    }
  }];
}

// Helper to create AI response output
export function createAIOutput(response: string, model: string, usage?: any, additionalData?: Record<string, any>): INodeExecutionData[] {
  return [{
    json: {
      response,
      model,
      usage,
      timestamp: new Date().toISOString(),
      ...additionalData
    }
  }];
}

// Helper to create location output
export function createLocationOutput(latitude: number, longitude: number, additionalData?: Record<string, any>): INodeExecutionData[] {
  return [{
    json: {
      latitude,
      longitude,
      timestamp: new Date().toISOString(),
      ...additionalData
    }
  }];
}