import {
  createAgentSession, createExtensionRuntime, ModelRuntime, SessionManager, SettingsManager,
  type ResourceLoader, type ToolDefinition, type SessionHeader,
} from "@earendil-works/pi-coding-agent";
import { type AssistantMessage, type Message, type ToolResultMessage } from "@earendil-works/pi-ai";
import { AssistantMessageEventStream } from "@earendil-works/pi-ai/utils/event-stream";
import { validateToolArguments } from "@earendil-works/pi-ai/utils/validation";
import { applyCanonicalToolResult, assertMessageWire, projectCanonical, sdkValidationNumbers, wireToPi } from "./codec.ts";
import { canonicalDigest } from "./protocol.ts";
import {
  DriverInterrupted, ProtocolError, SDK_VERSION, type HostOutput, type ModelResult, type Operation,
  type OperationPayload, type OperationResult, type RunTurn, type ToolResult, type MessageWire, type TurnCheckpoint,
} from "./types.ts";

export type Exchange = (operation: Operation, signal?: AbortSignal) => Promise<OperationResult>;

function emptyResources(): ResourceLoader {
  const runtime = createExtensionRuntime();
  return {
    getExtensions: () => ({extensions: [], errors: [], runtime}),
    getSkills: () => ({skills: [], diagnostics: []}),
    getPrompts: () => ({prompts: [], diagnostics: []}),
    getThemes: () => ({themes: [], diagnostics: []}),
    getAgentsFiles: () => ({agentsFiles: []}),
    getSystemPrompt: () => undefined,
    getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [],
    getAppendSystemPromptSources: () => [],
    extendResources: () => {},
    reload: async () => {},
  };
}

export function validateRun(input: RunTurn): void {
  if (input.protocol !== 1 || !Number.isSafeInteger(input.turn) || input.turn < 0 || !Number.isSafeInteger(input.epoch) || input.epoch < 0) {
    throw new ProtocolError("invalid_run", "Invalid Pi protocol or turn identity");
  }
  if (!input.scope || [input.scope.workflow_id, input.scope.generation_id, input.scope.node_id].some(value => typeof value !== "string" || !value)) {
    throw new ProtocolError("invalid_run", "Missing trusted Pi execution scope");
  }
  if (!Array.isArray(input.messages) || !Array.isArray(input.tools) || !input.model?.id || !input.model.provider || typeof input.cwd !== "string") {
    throw new ProtocolError("invalid_run", "Missing Pi model, transcript, tools, or workspace");
  }
  if ([input.context_digest, input.binding_digest, input.config_digest].some(value => !/^[a-f0-9]{64}$/.test(value))) {
    throw new ProtocolError("invalid_run", "Invalid Pi state digest");
  }
  if (canonicalDigest(input.messages) !== input.context_digest) throw new ProtocolError("context_mismatch", "Canonical context digest does not match its admitted state");
  const names = new Set<string>();
  for (const tool of input.tools) {
    if (!/^[a-zA-Z_][a-zA-Z0-9_.:-]*$/.test(tool.name) || names.has(tool.name)) throw new ProtocolError("invalid_tools", "Tool names must be unique exact names");
    names.add(tool.name);
    if (!tool.parameters || typeof tool.parameters !== "object" || Array.isArray(tool.parameters)) throw new ProtocolError("invalid_tools", "Tool parameters require an object schema");
  }
}

function toolWire(result: OperationResult, callId: string, name: string): ToolResult {
  if (!result || typeof result !== "object") throw new ProtocolError("result_mismatch", "Missing tool receipt");
  const decoded = "message" in result ? result as ToolResult : {message: result as MessageWire, is_error: false};
  assertMessageWire(decoded.message);
  if (decoded.message.role !== "tool" || decoded.message.tool_call_id !== callId || decoded.message.name !== name) {
    throw new ProtocolError("result_mismatch", "Tool receipt does not match the recorded call");
  }
  return decoded;
}

/** A full SDK session driving exactly one recorded assistant/tool round. */
export class TurnDriver {
  private session?: Awaited<ReturnType<typeof createAgentSession>>["session"];
  private interrupted?: DriverInterrupted;
  private fatal?: Error;
  private assistant?: MessageWire;
  private modelResult?: ModelResult;
  private tools = new Map<string, ToolResult>();
  private consumed = new Set<string>();
  private completed = false;
  private readonly cancelController = new AbortController();
  constructor(private readonly run: RunTurn, private readonly exchange: Exchange,
    private readonly notify: (event: HostOutput) => Promise<void> = async()=>{}) { validateRun(run); }

  interrupt(parked = false): void {
    if (this.completed) return;
    this.interrupted = new DriverInterrupted(parked);
    this.cancelController.abort();
    this.session?.agent.abort();
  }
  private payload(extra: Partial<OperationPayload>): OperationPayload {
    return {scope: this.run.scope, epoch: this.run.epoch, turn: this.run.turn,
      context_digest: this.run.context_digest, config_digest: this.run.config_digest, binding_digest: this.run.binding_digest, ...extra};
  }
  private async operation(kind: "model" | "tool", id: string, payload: OperationPayload, signal?: AbortSignal): Promise<OperationResult> {
    if (this.interrupted) throw this.interrupted;
    // A lease epoch fences attempts; it cannot change the logical operation's content.
    const {epoch: _epoch, ...logicalPayload} = payload;
    const operation: Operation = {type: "operation", id, kind, payload,
      fingerprint: canonicalDigest({id, kind, payload: logicalPayload})};
    try {
      const result = await this.exchange(operation, signal ? AbortSignal.any([signal, this.cancelController.signal]) : this.cancelController.signal);
      if (this.interrupted) throw this.interrupted;
      this.consumed.add(id);
      return result;
    } catch (error) {
      if (!(error instanceof DriverInterrupted)) this.fatal = error instanceof Error ? error : new ProtocolError("operation_failed", "Coordinator operation failed");
      throw error;
    }
  }

  private manager(): SessionManager {
    const snapshot = this.run.snapshot;
    let manager: SessionManager;
    if (snapshot && canonicalDigest(snapshot.scope)!==canonicalDigest(this.run.scope)) throw new ProtocolError("snapshot_scope_mismatch", "Pi snapshot belongs to another execution scope");
    if (snapshot && (snapshot.version !== 1 || snapshot.sdk_version !== SDK_VERSION)) {
      throw new ProtocolError("snapshot_incompatible", "Pi snapshot format or SDK version is incompatible with this runtime");
    }
    // The snapshot is a derived cache: canonical context/bindings/config decide reuse.
    if (snapshot && snapshot.context_digest === this.run.context_digest
      && snapshot.binding_digest === this.run.binding_digest && snapshot.config_digest === this.run.config_digest) {
      // A reusable SDK snapshot bypasses wireToPi, including its input validation.
      this.run.messages.forEach(assertMessageWire);
      if (!snapshot.header || !Array.isArray(snapshot.entries) || snapshot.entries.some(entry => !["message", "model_change", "thinking_level_change"].includes(entry.type))) {
        throw new ProtocolError("invalid_snapshot", "Unsupported Pi snapshot entries");
      }
      manager = SessionManager.inMemory(this.run.cwd, undefined, [structuredClone(snapshot.header), ...structuredClone(snapshot.entries)]);
      if (snapshot.leaf_id === null) manager.resetLeaf();
      else {
        if (!manager.getEntry(snapshot.leaf_id)) throw new ProtocolError("invalid_snapshot", "Pi snapshot leaf is absent");
        manager.branch(snapshot.leaf_id);
      }
      if (canonicalDigest(projectCanonical(manager.buildSessionContext().messages as Message[])) !== this.run.context_digest) {
        throw new ProtocolError("projection_mismatch", "Pi snapshot differs from canonical Context");
      }
    } else {
      manager = SessionManager.inMemory(this.run.cwd);
      for (const message of this.run.messages) manager.appendMessage(wireToPi(message, this.run.model));
    }
    return manager;
  }

  private stream = (_model: unknown, context: {messages: Message[]}, options?: {signal?: AbortSignal}): AssistantMessageEventStream => {
    const stream = new AssistantMessageEventStream();
    void (async () => {
      try {
        if (this.modelResult) throw new ProtocolError("extra_model_call", "Pi attempted more than one model request in a round");
        if (canonicalDigest(projectCanonical(context.messages)) !== this.run.context_digest) throw new ProtocolError("projection_mismatch", "Pi model context differs from admitted Context");
        const result = await this.operation("model", `turn:${this.run.turn}:model`, this.payload({purpose: "assistant"}), options?.signal) as ModelResult;
        if (!result?.assistant_message || result.assistant_message.role !== "assistant") throw new ProtocolError("result_mismatch", "Model receipt lacks a canonical assistant");
        const message = wireToPi(result.assistant_message, this.run.model, result) as AssistantMessage;
        const calls = result.assistant_message.tool_calls;
        if (calls.some(call => !call.id || !call.name) || new Set(calls.map(call => call.id)).size !== calls.length) throw new ProtocolError("invalid_calls", "Recorded tool call IDs must be unique and nonempty");
        this.modelResult = result;
        this.assistant = structuredClone(result.assistant_message);
        // Durable preflight arrives before callback-driven tools. The coordinator can safely
        // batch/park delegation only after recording these exact SDK validation outcomes.
        const verdicts=calls.map((call,call_index)=>{
          const tool=this.run.tools.find(tool=>tool.name===call.name);
          let error:string|undefined;
          if (message.stopReason==="length" || message.stopReason==="error" || message.stopReason==="aborted") error="Assistant response cannot execute tools";
          else if (call.parse_error) error="Invalid recorded tool arguments";
          else if (!tool) error="Recorded tool is not available";
          else {
            try {validateToolArguments({...tool,parameters:sdkValidationNumbers(tool.parameters)} as never,
              {type:"toolCall",id:call.id,name:call.name,arguments:sdkValidationNumbers(call.args) as never});}
            catch {error="Recorded tool arguments failed SDK validation";}
          }
          return {call_index,call_id:call.id,receipt_only:Boolean(error),...(error ? {error} : {})};
        });
        await this.notify({type:"assistant_ready",turn:this.run.turn,epoch:this.run.epoch,context_digest:this.run.context_digest,
          binding_digest:this.run.binding_digest,calls:verdicts});
        stream.push({type: "start", partial: message});
        if (message.stopReason === "error" || message.stopReason === "aborted") stream.push({type: "error", reason: message.stopReason, error: message});
        else stream.push({type: "done", reason: message.stopReason as "stop" | "length" | "toolUse", message});
        stream.end(message);
      } catch (error) {
        this.fatal = error instanceof Error ? error : new ProtocolError("model_bridge_failed", "Model bridge failed");
        const message: AssistantMessage = {role: "assistant", content: [], api: "opencompany-temporal", provider: "opencompany-temporal",
          model: this.run.model.id, usage: {input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},
          stopReason: this.interrupted ? "aborted" : "error", errorMessage: "Model bridge failed", timestamp: 0};
        // A failed stream still terminates; the driver rejects the synthetic transcript.
        stream.push({type: "error", reason: message.stopReason as "error" | "aborted", error: message});
        stream.end(message);
      }
    })();
    return stream;
  };

  private callIndex(callId: string): number {
    const index = this.assistant?.tool_calls.findIndex(call => call.id === callId) ?? -1;
    if (index < 0) throw new ProtocolError("unknown_call", "Pi produced an unrecorded tool call");
    return index;
  }
  private async tool(callId: string, receiptOnly: boolean, error?: string, signal?: AbortSignal): Promise<ToolResult> {
    const index = this.callIndex(callId);
    const call = this.assistant!.tool_calls[index]!;
    const existing = this.tools.get(callId);
    if (existing) return existing;
    const receipt = await this.operation("tool", `turn:${this.run.turn}:tool:${index}:${callId}`,
      this.payload({call_id:callId,call_index:index,receipt_only:receiptOnly,...(error ? {error} : {})}), signal);
    try {
      const result = toolWire(receipt, callId, call.name);
      this.tools.set(callId, result);
      return result;
    } catch (cause) {
      // Pi emits an error tool result after a failed callback. A corrupt receipt
      // is terminal; that event must not request the same operation again.
      if (cause instanceof ProtocolError) this.fatal = cause;
      throw cause;
    }
  }

  async execute(): Promise<TurnCheckpoint> {
    const manager = this.manager();
    const runtime = await ModelRuntime.create({modelsPath: null, allowModelNetwork:false, refreshOnCreate:false,
      credentials: {read:async()=>undefined,list:async()=>[],modify:async()=>{throw new ProtocolError("auth_disabled", "Pi credentials are disabled");},delete:async()=>{}}});
    runtime.registerProvider("opencompany-temporal", {api: "opencompany-temporal", baseUrl: "https://coordinator.invalid", apiKey: "coordinator-only", authHeader: false,
      streamSimple: this.stream, models: [{id:this.run.model.id,name:this.run.model.id,api:"opencompany-temporal",input:["text"],
        reasoning: this.run.model.reasoning ?? false, contextWindow:this.run.model.contextWindow ?? 200000, maxTokens:this.run.model.maxTokens ?? 4096,
        cost:{input:0,output:0,cacheRead:0,cacheWrite:0}}]});
    const model = runtime.getModel("opencompany-temporal", this.run.model.id);
    if (!model) throw new ProtocolError("model_unavailable", "Pi coordinator model is unavailable");
    const customTools: ToolDefinition[] = this.run.tools.map(tool => ({name:tool.name,label:tool.name,description:tool.description,
      parameters:sdkValidationNumbers(tool.parameters) as ToolDefinition["parameters"], prepareArguments:sdkValidationNumbers as ToolDefinition["prepareArguments"],
      executionMode:"sequential", execute:async(callId,_normalizedArgs,signal)=>{
        // Only the recorded native call identity crosses this boundary, never Pi's coerced arguments.
        const call = this.assistant!.tool_calls[this.callIndex(callId)]!;
        const result = await this.tool(callId, Boolean(call.parse_error), call.parse_error ? "Invalid recorded tool arguments" : undefined, signal);
        const pi = wireToPi(result.message,this.run.model) as ToolResultMessage;
        return {content:pi.content,details:{}};
      }}));
    const {session} = await createAgentSession({cwd:this.run.cwd,agentDir:this.run.cwd,modelRuntime:runtime,model,
      thinkingLevel:"off", noTools:"builtin", tools:this.run.tools.map(tool=>tool.name), customTools,
      sessionManager:manager,resourceLoader:emptyResources(),settingsManager:SettingsManager.inMemory({
        compaction:{enabled:false},retry:{enabled:false,provider:{maxRetries:0}},cacheWarming:"off",
      })});
    this.session=session;
    session.agent.toolExecution="sequential";
    const previousAfter = session.agent.afterToolCall;
    session.agent.afterToolCall=async(call,signal)=>{
      const previous = await previousAfter?.(call,signal);
      return {...previous,isError:this.tools.get(call.toolCall.id)?.is_error ?? previous?.isError};
    };
    const unsubscribe=session.agent.subscribe(async(event)=>{
      if (this.interrupted || this.fatal) return;
      try {
        if (event.type==="message_end" && event.message.role==="toolResult") {
          const target=event.message;
          const receipt=await this.tool(target.toolCallId,true,"Pi rejected the recorded tool call",session.agent.signal);
          applyCanonicalToolResult(target,receipt.message,this.run.model,receipt.is_error ?? target.isError);
        }
      } catch(error) { this.fatal=error instanceof Error ? error : new ProtocolError("receipt_failed","Tool receipt failed"); throw error; }
    });
    const previousFinish=session.agent.finishTurn;
    session.agent.finishTurn=async(turn,signal)=>{
      await previousFinish?.(turn,signal);
      if (this.interrupted || this.fatal) return {action:"end"};
      for (const call of this.assistant?.tool_calls ?? []) {
        if (!this.tools.has(call.id)) {
          const receipt=await this.tool(call.id,true,"Assistant response cannot execute tools",signal);
          // Error/aborted assistants skip Pi's batch. Record non-effect results explicitly.
          manager.appendMessage(wireToPi(receipt.message,this.run.model));
        }
      }
      return {action:"end"};
    };
    try {
      if (this.interrupted) throw this.interrupted;
      await session.agent.prompt([]);
      if (this.interrupted) throw this.interrupted;
      if (this.fatal) throw this.fatal;
      if (!this.assistant || !this.modelResult) throw new ProtocolError("missing_assistant","Pi did not consume a model receipt");
      const results=this.assistant.tool_calls.map(call=>{
        const result=this.tools.get(call.id);
        if (!result) throw new ProtocolError("missing_receipt","Pi round lacks a recorded tool receipt");
        return result.message;
      });
      const projection=projectCanonical(manager.buildSessionContext().messages as Message[]);
      const expected=[...this.run.messages,this.assistant,...results];
      if (canonicalDigest(projection)!==canonicalDigest(expected)) throw new ProtocolError("projection_mismatch","Pi completed transcript differs from canonical receipts");
      const digest=canonicalDigest(expected);
      const checkpoint:TurnCheckpoint={type:"turn_checkpoint",protocol:1,sdk_version:SDK_VERSION,scope:this.run.scope,turn:this.run.turn,epoch:this.run.epoch,
        snapshot:{version:1,sdk_version:SDK_VERSION,scope:this.run.scope,turn:this.run.turn,epoch:this.run.epoch,
        context_digest:digest,config_digest:this.run.config_digest,binding_digest:this.run.binding_digest,
        header:structuredClone(manager.getHeader() as SessionHeader),entries:structuredClone(manager.getEntries()),leaf_id:manager.getLeafId()},
        consumed_operations:[...this.consumed],assistant_message:structuredClone(this.assistant),tool_results:structuredClone(results),projection_digest:digest};
      this.completed=true;
      return checkpoint;
    } finally {
      unsubscribe();
      session.agent.abort();
      await session.agent.waitForIdle();
      session.dispose();
      this.session=undefined;
    }
  }
}
