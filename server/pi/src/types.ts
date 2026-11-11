import type { SessionEntry, SessionHeader } from "@earendil-works/pi-coding-agent";

export const PROTOCOL_VERSION = 1;
export const SDK_VERSION = "1.1.0";
export const MAX_FRAME_BYTES = 16 * 1024 * 1024;
export const INLINE_LIMIT_BYTES = 256 * 1024;

export interface ToolCallWire {
  id: string;
  name: string;
  args: Record<string, unknown>;
  raw_arguments: string | null;
  parse_error: string | null;
}
export interface BlockWire {
  type: string;
  text: string;
  tool_call: ToolCallWire | null;
  tool_call_id: string | null;
  name: string | null;
  metadata: Record<string, unknown>;
  source?: Record<string, unknown> | null;
}
/** OpenCompany's durable MessageWire. It remains the model broker's authority. */
export interface MessageWire {
  role: string;
  content: string;
  blocks: BlockWire[];
  tool_calls: ToolCallWire[];
  tool_call_id: string | null;
  name: string | null;
  provider_state: Record<string, unknown>;
  /** Conversation-view timestamp; preserved for projection, never SDK timing. */
  ts?: string;
}
export interface ToolWire {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}
export interface Scope {
  workflow_id: string;
  generation_id: string;
  node_id: string;
}
export interface ModelWire {
  id: string;
  provider: string;
  contextWindow?: number;
  maxTokens?: number;
  reasoning?: boolean;
}
export interface Snapshot {
  version: 1;
  sdk_version: "1.1.0";
  scope: Scope;
  turn: number;
  epoch: number;
  context_digest: string;
  binding_digest: string;
  config_digest: string;
  header: SessionHeader;
  entries: SessionEntry[];
  leaf_id: string | null;
}
export interface RunTurn {
  type: "run_turn";
  protocol: 1;
  turn: number;
  scope: Scope;
  epoch: number;
  context_digest: string;
  config_digest: string;
  binding_digest: string;
  cwd: string;
  blob_dir?: string;
  model: ModelWire;
  messages: MessageWire[];
  tools: ToolWire[];
  snapshot?: Snapshot | null;
}
export interface OperationPayload {
  scope: Scope;
  epoch: number;
  turn: number;
  context_digest: string;
  config_digest: string;
  binding_digest: string;
  purpose?: "assistant";
  call_id?: string;
  call_index?: number;
  receipt_only?: boolean;
  error?: string;
}
export interface Operation {
  type: "operation";
  id: string;
  kind: "model" | "tool";
  fingerprint: string;
  payload: OperationPayload;
}
export interface ModelResult {
  assistant_message: MessageWire;
  calls?: ToolCallWire[];
  usage?: Record<string, number>;
  finish_reason?: string;
  kind?: string;
}
export interface ToolResult {
  message: MessageWire;
  is_error?: boolean;
}
export type OperationResult = ModelResult | ToolResult | MessageWire;
export interface TurnCheckpoint {
  type: "turn_checkpoint";
  protocol: 1;
  sdk_version: "1.1.0";
  scope: Scope;
  turn: number;
  epoch: number;
  snapshot: Snapshot;
  consumed_operations: string[];
  assistant_message: MessageWire;
  tool_results: MessageWire[];
  projection_digest: string;
}
export interface AssistantReady {
  type: "assistant_ready";
  turn: number;
  epoch: number;
  context_digest: string;
  binding_digest: string;
  calls: {call_index: number;call_id: string;receipt_only: boolean;error?: string}[];
}
export interface FileReference {
  pi_file: {name:string;sha256:string;bytes:number;scope_digest:string;epoch:number};
}
export interface ProtocolEnvelope {
  type: string;
  pi_payload: FileReference;
  protocol?: 1;
  scope?: Scope;
  epoch?: number;
  blob_dir?: string;
}
export type HostInput = RunTurn | ProtocolEnvelope | { type: "operation_result"; id: string; result: OperationResult }
  | { type: "abort" | "park" | "keepalive" };
export type HostOutput = Operation | TurnCheckpoint | AssistantReady | { type: "parked"; turn: number; epoch: number }
  | { type: "aborted" } | { type: "error"; code: string; message: string };

export class ProtocolError extends Error {
  constructor(public readonly code: string, message: string) { super(message); }
}
export class DriverInterrupted extends Error {
  constructor(public readonly parked: boolean) { super(parked ? "Driver parked" : "Driver aborted"); }
}
