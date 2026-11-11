import type { Message, AssistantMessage, Usage, ToolResultMessage } from "@earendil-works/pi-ai";
import { ProtocolError, type BlockWire, type MessageWire, type ModelWire, type ToolCallWire } from "./types.ts";

type PiMessage = Message & { _oc?: MessageMetadata };
interface MessageMetadata {
  version: 1;
  provider_state: Record<string, unknown>;
  content?: string;
  call_order: string[];
  tool_call_id: string | null;
  name: string | null;
  had_blocks: boolean;
  ts?: string;
}
interface ContentMetadata {
  type: string;
  tool_call_id: string | null;
  name: string | null;
  metadata: Record<string, unknown>;
  source?: Record<string, unknown> | null;
  call?: { raw_arguments: string | null; parse_error: string | null };
  derived?: boolean;
}
type PiContent = Record<string, unknown> & { _ocBlock?: ContentMetadata };
const EMPTY_USAGE: Usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };

/** SDK-only numeric validation view. The canonical bigint remains in the transcript.
 * Native Pydantic validation still checks the exact original integer before effects. */
export function sdkValidationNumbers(value:unknown):unknown {
  if(typeof value==="bigint") return Number(value);
  if(Array.isArray(value)) return value.map(sdkValidationNumbers);
  if(value && typeof value==="object") return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,sdkValidationNumbers(item)]));
  return value;
}

export function usageToPi(value: Record<string, number> = {}): Usage {
  return { ...structuredClone(EMPTY_USAGE), input: value.input_tokens ?? 0, output: value.output_tokens ?? 0,
    cacheRead: value.cache_read_tokens ?? 0, cacheWrite: value.cache_creation_tokens ?? 0,
    reasoning: value.reasoning_tokens ?? 0, totalTokens: value.total_tokens ?? ((value.input_tokens ?? 0) + (value.output_tokens ?? 0)) };
}

function blockMetadata(block: BlockWire): ContentMetadata {
  if (block.source?.kind === "bytes") throw new ProtocolError("transient_media", "Hydrated media cannot enter a Pi snapshot");
  return { type: block.type, tool_call_id: block.tool_call_id, name: block.name,
    metadata: structuredClone(block.metadata), ...(block.source === undefined ? {} : {source: structuredClone(block.source)}),
    ...(block.tool_call ? {call: {raw_arguments: block.tool_call.raw_arguments, parse_error: block.tool_call.parse_error}} : {}) };
}
function callContent(call: ToolCallWire): PiContent {
  return { type: "toolCall", id: call.id, name: call.name, arguments: structuredClone(call.args) };
}

/** Only minimal reconstruction metadata is added; provider state is never rewritten. */
export function wireToPi(wire: MessageWire, model: ModelWire, options: {
  usage?: Record<string, number>; finish_reason?: string;
} = {}): PiMessage {
  if (!["system", "user", "assistant", "tool"].includes(wire.role)) throw new ProtocolError("invalid_role", "Unsupported canonical message role");
  const blocks: PiContent[] = wire.blocks.map(block => {
    const meta = blockMetadata(block);
    if (block.type === "tool_call" && block.tool_call) return { ...callContent(block.tool_call), _ocBlock: meta };
    if (block.type === "reasoning") return { type: "thinking", thinking: block.text,
      ...(typeof block.metadata.signature === "string" ? {thinkingSignature: block.metadata.signature} : {}), _ocBlock: meta };
    // Media remains a durable descriptor. The Python model broker alone hydrates it.
    return { type: "text", text: block.text, _ocBlock: meta };
  });
  if (!blocks.length && wire.content) blocks.push({type: "text", text: wire.content, _ocBlock: {
    type: wire.role === "tool" ? "tool_result" : "text", tool_call_id: wire.tool_call_id, name: wire.name, metadata: {}, derived: true,
  }});
  if (wire.role === "assistant") {
    const present = new Set(blocks.filter(block => block.type === "toolCall").map(block => block.id));
    for (const call of wire.tool_calls) if (!present.has(call.id)) blocks.push({...callContent(call), _ocBlock: {
      type: "tool_call", tool_call_id: null, name: null, metadata: {}, call: {raw_arguments: call.raw_arguments, parse_error: call.parse_error}, derived: true,
    }});
  }
  const flattened = blocks.filter(block => block._ocBlock?.type === "text" || block._ocBlock?.type === "tool_result").map(block => String(block.text ?? "")).join("");
  const _oc: MessageMetadata = { version: 1, provider_state: structuredClone(wire.provider_state),
    ...(wire.content === flattened ? {} : {content: wire.content}), call_order: wire.tool_calls.map(call => call.id),
    ...(Object.hasOwn(wire, "ts") ? {ts: wire.ts} : {}),
    tool_call_id: wire.tool_call_id, name: wire.name, had_blocks: wire.blocks.length > 0 };
  if (wire.role === "assistant") {
    const reason = options.finish_reason;
    const stopReason = reason === "length" || reason === "max_tokens" ? "length" : reason === "error" || reason === "aborted" ? reason
      : wire.tool_calls.length ? "toolUse" : "stop";
    return {role: "assistant", content: blocks, api: "opencompany-temporal", provider: "opencompany-temporal",
      model: model.id, usage: usageToPi(options.usage), stopReason, timestamp: 0, _oc} as unknown as PiMessage;
  }
  if (wire.role === "tool") return {role: "toolResult", toolCallId: wire.tool_call_id ?? "", toolName: wire.name ?? "",
    content: blocks, isError: false, timestamp: 0, _oc} as unknown as PiMessage;
  return {role: wire.role, content: blocks, timestamp: 0, _oc} as unknown as PiMessage;
}

export function piToWire(message: Message): MessageWire {
  const pi = message as PiMessage;
  if (!pi._oc || pi._oc.version !== 1) throw new ProtocolError("projection_mismatch", "Pi message lacks canonical reconstruction metadata");
  const content = (typeof pi.content === "string" ? [{type: "text", text: pi.content}] : pi.content) as unknown as PiContent[];
  const blocks: BlockWire[] = [];
  const calls = new Map<string, ToolCallWire>();
  for (const part of content) {
    const metadata = part._ocBlock;
    if (!metadata) throw new ProtocolError("projection_mismatch", "Pi message contains unrecorded content");
    const call = part.type === "toolCall" ? { id: String(part.id), name: String(part.name), args: structuredClone(part.arguments as Record<string, unknown>),
      raw_arguments: metadata.call?.raw_arguments ?? null, parse_error: metadata.call?.parse_error ?? null } : null;
    if (call) calls.set(call.id, call);
    if (!metadata.derived) blocks.push({type: metadata.type, text: part.type === "thinking" ? String(part.thinking ?? "") : String(part.text ?? ""),
      tool_call: call, tool_call_id: metadata.tool_call_id, name: metadata.name, metadata: structuredClone(metadata.metadata),
      ...(metadata.source === undefined ? {} : {source: structuredClone(metadata.source)})});
  }
  return { role: pi.role === "toolResult" ? "tool" : pi.role,
    content: pi._oc.content ?? content.filter(part => part._ocBlock?.type === "text" || part._ocBlock?.type === "tool_result").map(part => String(part.text ?? "")).join(""),
    blocks: pi._oc.had_blocks ? blocks : [], tool_calls: pi._oc.call_order.map(id => {
      const call = calls.get(id);
      if (!call) throw new ProtocolError("projection_mismatch", "Pi transcript lost a canonical tool call");
      return call;
    }), tool_call_id: pi._oc.tool_call_id, name: pi._oc.name, provider_state: structuredClone(pi._oc.provider_state),
    ...(Object.hasOwn(pi._oc, "ts") ? {ts: pi._oc.ts} : {}) };
}

/** Tool declaration-only system entries are derived by AgentSession, never policy. */
export function projectCanonical(messages: Message[]): MessageWire[] {
  return messages.filter(message => {
    if ((message as PiMessage)._oc) return true;
    if (message.role === "system" && (message.content === "" || (Array.isArray(message.content) && message.content.length === 0)) && !message.sections) return false;
    throw new ProtocolError("projection_mismatch", "Unexpected noncanonical Pi transcript entry");
  }).map(piToWire);
}

export function isAssistant(message: Message): message is AssistantMessage { return message.role === "assistant"; }
export function applyCanonicalToolResult(target: ToolResultMessage, wire: MessageWire, model: ModelWire, isError: boolean): void {
  const result = wireToPi(wire, model) as ToolResultMessage;
  Object.assign(target, result, {isError});
}
