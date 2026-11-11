import { expect, test } from "bun:test";
import { TurnDriver } from "../src/driver.ts";
import { canonicalDigest, encodeFrame, losslessParse } from "../src/protocol.ts";
import { piToWire, projectCanonical, wireToPi } from "../src/codec.ts";
import { DriverInterrupted, type Operation, type ModelResult } from "../src/types.ts";
import {call,message,run,toolResult} from "./fixtures.ts";

test("full SDK executes one model/tool round and snapshots exact canonical wires", async()=>{
  const input=run(); const assistant=message("assistant","Reading",[call()]);
  assistant.provider_state={anthropic:{signature:"opaque+/=",encrypted:{__opencompany_bytes_base64__:"AQID"}}};
  const operations:Operation[]=[];
  const checkpoint=await new TurnDriver(input,async(op)=>{ operations.push(op);return op.kind==="model" ? {assistant_message:assistant,usage:{input_tokens:5,output_tokens:2}} : {message:toolResult()}; }).execute();
  expect(operations.map(op=>op.kind)).toEqual(["model","tool"]);
  expect(operations[1]!.payload).toMatchObject({call_index:0,call_id:"c1",receipt_only:false});
  expect(checkpoint.assistant_message).toEqual(assistant);
  expect(checkpoint.projection_digest).toBe(canonicalDigest([...input.messages,assistant,toolResult()]));
  expect(projectCanonical(checkpoint.snapshot.entries.filter(entry=>entry.type==="message").map(entry=>entry.message) as never)).toHaveLength(4);
});

test("snapshot rebuild and binding refresh add no blank user or duplicate policy", async()=>{
  const initial=run(); const a=message("assistant","first");
  const first=await new TurnDriver(initial,async()=>({assistant_message:a})).execute();
  const next=run([...initial.messages,a]); next.turn=1;next.snapshot=first.snapshot;next.tools=[];
  const second=await new TurnDriver(next,async()=>({assistant_message:message("assistant","second")})).execute();
  expect(second.snapshot.entries.filter(entry=>entry.type==="message" && entry.message.role==="user")).toHaveLength(1);
  const rebind=run([...next.messages,second.assistant_message]);rebind.turn=2;rebind.snapshot=second.snapshot;rebind.binding_digest=canonicalDigest(["new"]);
  const third=await new TurnDriver(rebind,async()=>({assistant_message:message("assistant","third")})).execute();
  expect(projectCanonical(third.snapshot.entries.filter(entry=>entry.type==="message").map(entry=>entry.message) as never).filter(wire=>wire.role==="system")).toHaveLength(1);
});

test.each(["unknown","validation","parse","length"])("%s tool calls get non-effect coordinator receipts",async(mode)=>{
  const c=call();
  if (mode==="unknown") c.name="missing";
  if (mode==="validation") c.args={file_path:{unexpected:true}};
  if (mode==="parse") {c.args={};c.parse_error="Invalid JSON";c.raw_arguments="{";}
  const assistant=message("assistant","",[c]); const ops:Operation[]=[];
  const output=await new TurnDriver(run(),async(op)=>{ops.push(op);return op.kind==="model" ? {assistant_message:assistant,finish_reason:mode==="length"?"length":"tool_calls"} : {message:toolResult(c.id,c.name,"rejected"),is_error:true};}).execute();
  expect(ops[1]!.payload.receipt_only).toBe(true);
  expect(output.tool_results[0]!.content).toBe("rejected");
});

test("coerced SDK arguments never cross the authority boundary",async()=>{
  const input=run();input.tools[0]!.parameters={type:"object",properties:{file_path:{type:"string"},flag:{type:"boolean"}},required:["file_path"]};
  const original=call("c1","file_read",{file_path:"a",flag:"true"});let toolOp:Operation|undefined;
  await new TurnDriver(input,async(op)=>{if(op.kind==="model")return {assistant_message:message("assistant","",[original])};toolOp=op;return toolResult();}).execute();
  expect(toolOp!.payload).not.toHaveProperty("args");
  expect(toolOp!.payload).toMatchObject({call_id:"c1",call_index:0});
});

test("duplicate recorded calls fail before effects",async()=>{
  const kinds:string[]=[];
  const driver=new TurnDriver(run(),async(op)=>{kinds.push(op.kind);return {assistant_message:message("assistant","",[call(),call()])};});
  await expect(driver.execute()).rejects.toMatchObject({code:"invalid_calls"});
  expect(kinds).toEqual(["model"]);
});

test("error assistant unresolved calls still reconcile non-effect receipts",async()=>{
  const input=run();const assistant=message("assistant","failed",[call()]);const ops:Operation[]=[];
  const checkpoint=await new TurnDriver(input,async(op)=>{ops.push(op);return op.kind==="model" ? {assistant_message:assistant,finish_reason:"error"} : {message:toolResult("c1","file_read","unexecuted"),is_error:true};}).execute();
  expect(ops[1]!.payload.receipt_only).toBe(true);expect(checkpoint.tool_results).toHaveLength(1);
});

test("media refs and provider metadata round trip without hydrated bytes",()=>{
  const wire=message("user","look");wire.provider_state={provider:{opaque:["signature",{id:"rid"}]}};
  wire.blocks.push({type:"image",text:"",tool_call:null,tool_call_id:null,name:null,metadata:{mime_type:"image/png"},source:{kind:"file_ref",file_ref:{id:"f1",path:"a.png"}}});
  expect(piToWire(wireToPi(wire,{id:"m",provider:"p"}))).toEqual(wire);
  wire.blocks[1]!.source={kind:"bytes",data:"AAAA"};
  expect(()=>wireToPi(wire,{id:"m",provider:"p"})).toThrow("Hydrated media");
});

test("stored view timestamps preserve exact historical calls and opaque metadata",()=>{
  const historical=call("historical","file_read",{id:9007199254740993n,ts:{opaque:"argument timestamp"}});
  historical.raw_arguments='{"id":9007199254740993,"ts":';historical.parse_error="Invalid JSON";
  const assistant=message("assistant","",[historical]);
  assistant.provider_state={provider:{ts:{signature:"opaque+/="},id:9007199254740993n}};
  assistant.blocks[0]!.metadata={ts:{opaque:"block timestamp"}};
  const wires=[message("system","Policy"),message("user","Earlier request"),assistant,toolResult("historical","file_read","Invalid JSON")];
  for(const wire of wires) {
    wire.ts="2026-11-11T09:12:00+00:00";
    const converted=wireToPi(wire,{id:"m",provider:"p"});
    expect(converted.timestamp).toBe(0);
    expect(converted).not.toHaveProperty("ts");
    expect(piToWire(converted)).toEqual(wire);
  }
  const unstamped=message("user","New request");
  expect(piToWire(wireToPi(unstamped,{id:"m",provider:"p"}))).toEqual(unstamped);
  expect(piToWire(wireToPi(unstamped,{id:"m",provider:"p"}))).not.toHaveProperty("ts");
});

test("a restored stamped conversation completes a round and reuses its exact snapshot",async()=>{
  const historical=call("historical");historical.raw_arguments="{";historical.parse_error="Invalid JSON";historical.args={};
  const stored=[message("user","Earlier request"),message("assistant","",[historical]),
    toolResult("historical","file_read","Invalid JSON"),message("assistant","Previous response")];
  for(const [index,wire] of stored.entries()) wire.ts=`2026-11-11T09:12:0${index}+00:00`;
  const input=run([message("system","Current policy"),...stored,message("user","Read the current file")]);
  const assistant=message("assistant","Reading",[call("current")]);const result=toolResult("current");
  const operations:Operation[]=[];
  const first=await new TurnDriver(input,async(op)=>{
    operations.push(op);
    return op.kind==="model" ? {assistant_message:assistant} : {message:result};
  }).execute();
  expect(operations.map(op=>op.id)).toEqual(["turn:0:model","turn:0:tool:0:current"]);
  const completed=[...input.messages,assistant,result];
  expect(first.projection_digest).toBe(canonicalDigest(completed));
  const restored=losslessParse(encodeFrame(first.snapshot)) as typeof first.snapshot;
  const next=run(completed);next.turn=1;next.snapshot=restored;
  const second=await new TurnDriver(next,async(op)=>{
    operations.push(op);return {assistant_message:message("assistant","Done")};
  }).execute();
  expect(operations.map(op=>op.id)).toEqual(["turn:0:model","turn:0:tool:0:current","turn:1:model"]);
  expect(projectCanonical(second.snapshot.entries.filter(entry=>entry.type==="message").map(entry=>entry.message) as never))
    .toEqual([...completed,second.assistant_message]);

  const corrupted=structuredClone(next);
  const stamped=corrupted.snapshot!.entries.find(entry=>entry.type==="message" && entry.message.role==="user");
  (stamped as unknown as {message:{_oc:{ts:string}}}).message._oc.ts="changed";
  let requests=0;
  await expect(new TurnDriver(corrupted,async()=>{requests++;return {assistant_message:message("assistant","unexpected")};}).execute())
    .rejects.toMatchObject({code:"projection_mismatch"});
  expect(requests).toBe(0);
});

test("parking disposes a waiting SDK session without an aborted checkpoint",async()=>{
  let requested!:()=>void;const started=new Promise<void>(resolve=>{requested=resolve;});
  const driver=new TurnDriver(run(),async(_op,signal)=>new Promise<ModelResult>((_resolve,reject)=>{requested();signal!.addEventListener("abort",()=>reject(new DriverInterrupted(true)),{once:true});}));
  const executing=driver.execute();await started;driver.interrupt(true);
  await expect(executing).rejects.toMatchObject({parked:true});
});

test("reconstruction reuses stable receipts across driver epochs",async()=>{
  const cache=new Map<string,{fingerprint:string,result:ReturnType<typeof message>|ModelResult}>();
  let effects=0;const original=run();let parked!:()=>void;
  const requests:Operation[]=[];
  const first=new TurnDriver(original,async(op,signal)=>{
    requests.push(op);
    if(op.id.includes(":tool:1:")) return await new Promise<never>((_resolve,reject)=>{parked();signal!.addEventListener("abort",()=>reject(new DriverInterrupted(true)),{once:true});});
    const result=op.kind==="model" ? {assistant_message:message("assistant","",[call("a"),call("b")])} : toolResult("a");
    cache.set(op.id,{fingerprint:op.fingerprint,result});if(op.kind==="tool")effects++;return result;
  });
  const pending=new Promise<void>(resolve=>{parked=resolve;});const failed=first.execute();await pending;first.interrupt(true);
  await expect(failed).rejects.toMatchObject({parked:true});
  const replay={...original,epoch:2};
  const recovered=await new TurnDriver(replay,async(op)=>{
    const receipt=cache.get(op.id);
    if(receipt) {expect(op.fingerprint).toBe(receipt.fingerprint);return receipt.result;}
    effects++;return toolResult("b");
  }).execute();
  expect(effects).toBe(2);expect(recovered.tool_results.map(wire=>wire.tool_call_id)).toEqual(["a","b"]);
});

test("durable SDK preflight precedes callbacks and preserves original argument typing",async()=>{
  const events:string[]=[];const c=call("a","file_read",{file_path:{bad:true}});
  const driver=new TurnDriver(run(),async(op)=>{events.push(op.kind);return op.kind==="model" ? {assistant_message:message("assistant","",[c])} : {message:toolResult("a","file_read","invalid"),is_error:true};},async(event)=>{
    events.push(event.type);expect(event).toMatchObject({type:"assistant_ready",calls:[{call_id:"a",call_index:0,receipt_only:true}]});
  });
  await driver.execute();expect(events).toEqual(["model","assistant_ready","tool"]);expect(c.args.file_path).toEqual({bad:true});
});

test("corrupt, stale-scope and unrecorded snapshot content cannot become Context",async()=>{
  const input=run();const first=await new TurnDriver(input,async()=>({assistant_message:message("assistant","done")})).execute();
  const next=run([...input.messages,first.assistant_message]);next.snapshot=structuredClone(first.snapshot);next.turn=1;
  next.snapshot.scope.workflow_id="other";
  await expect(new TurnDriver(next,async()=>({assistant_message:message("assistant","done")})).execute()).rejects.toMatchObject({code:"snapshot_scope_mismatch"});
  next.snapshot=structuredClone(first.snapshot);next.snapshot.leaf_id="absent";
  await expect(new TurnDriver(next,async()=>({assistant_message:message("assistant","done")})).execute()).rejects.toMatchObject({code:"invalid_snapshot"});
});

test.each(["version","sdk_version"])("incompatible snapshot %s is terminal even when canonical state needs rebuilding",async(field)=>{
  const input=run();
  const first=await new TurnDriver(input,async()=>({assistant_message:message("assistant","done")})).execute();
  const next=run([...input.messages,first.assistant_message]);next.turn=1;next.snapshot=structuredClone(first.snapshot);
  (next.snapshot as unknown as Record<string,unknown>)[field]=field==="version" ? 999 : "999.0.0";
  next.config_digest=canonicalDigest({model:"changed"});
  let requests=0;
  await expect(new TurnDriver(next,async()=>{requests++;return {assistant_message:message("assistant","done")};}).execute())
    .rejects.toMatchObject({code:"snapshot_incompatible"});
  expect(requests).toBe(0);
});

test.each(["context_digest","binding_digest","config_digest"])("supported snapshots rebuild on changed %s",async(field)=>{
  const input=run();
  const first=await new TurnDriver(input,async()=>({assistant_message:message("assistant","first")})).execute();
  const next=run([...input.messages,first.assistant_message]);next.turn=1;next.snapshot=structuredClone(first.snapshot);
  next.snapshot[field]=canonicalDigest({stale:field});
  const output=await new TurnDriver(next,async()=>({assistant_message:message("assistant","next")})).execute();
  expect(output.projection_digest).toBe(canonicalDigest([...next.messages,output.assistant_message]));
  expect(output.snapshot.entries.filter(entry=>entry.type==="message" && entry.message.role==="user")).toHaveLength(1);
});

test("unsafe provider and argument integers survive SDK validation and snapshot replay",async()=>{
  const input=run();input.tools[0]!.parameters={type:"object",properties:{id:{type:"integer"}},required:["id"]};
  const c=call("c1","file_read",{id:9007199254740993n});c.raw_arguments='{"id":9007199254740993}';
  const assistant=message("assistant","",[c]);assistant.provider_state={provider:{id:9007199254740993n,"😀":2,"\uE000":1}};
  const wireResult=toolResult();wireResult.provider_state={native:{id:9007199254740993n}};
  const output=await new TurnDriver(input,async(op)=>op.kind==="model" ? {assistant_message:assistant} : {message:wireResult}).execute();
  expect(output.assistant_message).toEqual(assistant);expect(output.tool_results[0]!.provider_state).toEqual(wireResult.provider_state);
  const restored=losslessParse(encodeFrame(output)) as typeof output;
  expect(restored.snapshot.entries).toEqual(output.snapshot.entries);
  const next=run([...input.messages,assistant,wireResult]);next.snapshot=restored.snapshot;next.turn=1;next.tools=input.tools;
  const round=await new TurnDriver(next,async()=>({assistant_message:message("assistant","done")})).execute();
  expect(round.projection_digest).toBe(canonicalDigest([...next.messages,message("assistant","done")]));
});
