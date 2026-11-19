import {expect,test} from "bun:test";
import {join,dirname,resolve,sep} from "node:path";
import {fileURLToPath} from "node:url";
import {createInterface} from "node:readline";
import {Readable} from "node:stream";
import {encodeFrame,losslessParse,canonicalDigest} from "../src/protocol.ts";
import {ScopedProtocolBlobs} from "../src/transport.ts";
import {INLINE_LIMIT_BYTES,MAX_FRAME_BYTES} from "../src/types.ts";
import {mkdtemp,realpath,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {message,run} from "./fixtures.ts";
import {captureProcess,smokeHost} from "./process-helpers.ts";

const source=join(dirname(fileURLToPath(import.meta.url)),"..","src","index.ts");
const fixtures=join(dirname(fileURLToPath(import.meta.url)),"runtime-fixtures");

async function within<T>(promise:Promise<T>,milliseconds=10000):Promise<T> {
  let timer:ReturnType<typeof setTimeout>|undefined;
  try {return await Promise.race([promise,new Promise<never>((_resolve,reject)=>{
    timer=setTimeout(()=>reject(new Error("Pi host did not terminate within its cleanup bound")),milliseconds);
  })]);} finally {if(timer)clearTimeout(timer);}
}

test.each(["expired-clock.ts","terminate.ts"])("bootstrap host exits on %s while stdin remains open",async(preload)=>{
  const child=Bun.spawn([process.execPath,"--preload",join(fixtures,preload),source],{stdin:"pipe",stdout:"pipe",stderr:"pipe"});
  const stdout=new Response(child.stdout).text();const stderr=new Response(child.stderr).text();
  try {
    expect(await within(child.exited)).toBe(0);
    expect(await stdout).toBe("");expect(await stderr).toBe("");
  } finally {if(child.exitCode===null){child.kill();await child.exited;}}
},15000);

test.each(["expired-clock", "terminate"])("active host aborts on %s and drains its task before exit", async (trigger) => {
  const child = Bun.spawn([process.execPath, "--preload", join(fixtures, "active-interrupt.ts"), source], {
    stdin: "pipe", stdout: "pipe", stderr: "pipe", ipc() {},
  });
  const stderr = new Response(child.stderr).text();
  const readFrames = async () => {
    const types: string[] = [];
    for await (const line of createInterface({input: Readable.from(child.stdout)})) {
      const {type} = losslessParse(line) as {type: string};
      types.push(type);
      if (type === "operation") child.send(trigger);
    }
    return types;
  };
  try {
    child.stdin.write(encodeFrame(run()));
    const [code, types, errors] = await within(Promise.all([child.exited, readFrames(), stderr]));
    expect(errors).toBe("");
    expect(code).toBe(0);
    expect(types).toEqual(["operation", "aborted"]);
  } finally {if(child.exitCode===null){child.kill();await child.exited;}}
},15000);

test("bootstrap protocol failures still emit a bounded terminal error",async()=>{
  const child=Bun.spawn([process.execPath,source],{stdin:"pipe",stdout:"pipe",stderr:"pipe"});
  const stdout=new Response(child.stdout).text();const stderr=new Response(child.stderr).text();
  try {
    child.stdin.write(encodeFrame({type:"unknown"}));
    expect(await within(child.exited)).toBe(1);
    expect(losslessParse((await stdout).trim())).toMatchObject({type:"error",code:"unknown_frame"});
    expect(await stderr).toBe("");
  } finally {if(child.exitCode===null){child.kill();await child.exited;}}
},15000);
test("subprocess capture drains full stdout and stderr pipes before waiting for exit",async()=>{
  const size=1024*1024;
  const child=await captureProcess([process.execPath,"-e",`process.stdout.write("o".repeat(${size}));process.stderr.write("e".repeat(${size}));`],"Verbose archive command",10000);
  expect(child.exitCode).toBe(0);expect(child.stdout).toBe("o".repeat(size));expect(child.stderr).toBe("e".repeat(size));
},15000);
test("subprocess capture reports its phase and reaps a child that exceeds its bound",async()=>{
  let failure:Error|undefined;
  try {
    await captureProcess([process.execPath,"-e","process.stderr.write(`pid=${process.pid}`);setInterval(()=>{},1000);"],"Stalled archive command",2000);
  } catch(error) {failure=error as Error;}
  expect(failure?.message).toContain("Stalled archive command exceeded 2000 ms");
  const pid=Number(failure?.message.match(/pid=(\d+)/)?.[1]);
  expect(Number.isSafeInteger(pid) && pid>0).toBe(true);
  expect(()=>process.kill(pid,0)).toThrow();
},10000);
test("isolated JSONL host completes and exits without stdin EOF",()=>smokeHost(source),15000);
test("stdin EOF aborts and reaps a waiting SDK host",async()=>{
  const child=Bun.spawn([process.execPath,source],{stdin:"pipe",stdout:"pipe",stderr:"pipe"});
  child.stdin.write(encodeFrame(run()));
  const reader=child.stdout.getReader();const first=await reader.read();expect(new TextDecoder().decode(first.value)).toContain('"type":"operation"');
  child.stdin.end();
  let rest="";for(;;){const next=await reader.read();if(next.done)break;rest+=new TextDecoder().decode(next.value);}
  expect(rest).toContain('"type":"aborted"');expect(await child.exited).toBe(0);
},15000);
test("park control returns successful maintenance completion",async()=>{
  const child=Bun.spawn([process.execPath,source],{stdin:"pipe",stdout:"pipe",stderr:"pipe"});
  child.stdin.write(encodeFrame(run()));
  const reader=child.stdout.getReader();await reader.read();child.stdin.write(encodeFrame({type:"park"}));
  let rest="";for(;;){const next=await reader.read();if(next.done)break;rest+=new TextDecoder().decode(next.value);}
  expect(rest).toContain('"type":"parked"');expect(rest).not.toContain("turn_checkpoint");expect(await child.exited).toBe(0);
},15000);

test("large run, receipts and checkpoint use scoped refs within the inline frame bound",async()=>{
  const temp=await realpath(tmpdir());const root=await realpath(await mkdtemp(join(temp,"opencompany-pi-large-round-")));
  let child:ReturnType<typeof Bun.spawn>|undefined;
  try {
    const input=run();input.blob_dir=root;input.messages[1]!.provider_state={large:"u".repeat(INLINE_LIMIT_BYTES),id:9007199254740993n};
    input.context_digest=canonicalDigest(input.messages);
    const blobs=await ScopedProtocolBlobs.create(root,input.scope,input.epoch);
    const encoded=await blobs.encode(input);
    const frame=encodeFrame({type:"run_turn",protocol:1,scope:input.scope,epoch:input.epoch,blob_dir:root,pi_payload:encoded});
    expect(Buffer.byteLength(frame)).toBeLessThan(INLINE_LIMIT_BYTES);
    child=Bun.spawn([process.execPath,source],{stdin:"pipe",stdout:"pipe",stderr:"pipe"});
    (child.stdin as Bun.FileSink).write(frame);let buffer="";let checkpointSeen=false;let refSeen=false;
    for await(const chunk of child.stdout as ReadableStream<Uint8Array>) {
      buffer+=new TextDecoder().decode(chunk);let lf:number;
      while((lf=buffer.indexOf("\n"))>=0) {
        const raw=buffer.slice(0,lf);buffer=buffer.slice(lf+1);expect(Buffer.byteLength(raw)).toBeLessThan(INLINE_LIMIT_BYTES);
        let event=losslessParse(raw) as Record<string,unknown>;
        if(event.pi_payload) {refSeen=true;event=await blobs.decode(event.pi_payload) as Record<string,unknown>;}
        if(event.type==="operation") {
          const response={type:"operation_result",id:event.id,result:{assistant_message:message("assistant","done")}};
          (response.result.assistant_message.provider_state as Record<string,unknown>).large="a".repeat(INLINE_LIMIT_BYTES);
          const reference=await blobs.encode(response);
          (child.stdin as Bun.FileSink).write(encodeFrame({type:"operation_result",pi_payload:reference}));
        }
        if(event.type==="turn_checkpoint") {checkpointSeen=true;expect(event).not.toHaveProperty("assistant_message");expect(event).not.toHaveProperty("tool_results");}
        if(event.type==="error") throw new Error(`${event.code}: ${event.message}`);
      }
    }
    expect(checkpointSeen).toBe(true);expect(refSeen).toBe(true);expect(await child.exited).toBe(0);
  } finally {
    if(child && child.exitCode===null)child.kill();
    const absolute=resolve(root);
    if(!absolute.startsWith(resolve(temp)+sep))throw new Error("Transport test cleanup path escaped its temporary directory");
    await rm(absolute,{recursive:true,force:true});
  }
},15000);

test("an oversized complete snapshot emits a terminal protocol error after the failed write",async()=>{
  const temp=await realpath(tmpdir());const root=await realpath(await mkdtemp(join(temp,"opencompany-pi-oversized-checkpoint-")));
  let child:ReturnType<typeof Bun.spawn>|undefined;
  try {
    const input=run();input.blob_dir=root;
    input.messages[1]!.provider_state={private_payload:"u".repeat(9*1024*1024)};
    input.context_digest=canonicalDigest(input.messages);
    const assistant=message("assistant","done");assistant.provider_state={private_payload:"a".repeat(9*1024*1024)};
    const response={type:"operation_result",id:`turn:${input.turn}:model`,result:{assistant_message:assistant}};
    expect(Buffer.byteLength(encodeFrame(input))).toBeLessThan(MAX_FRAME_BYTES);
    expect(Buffer.byteLength(encodeFrame(response))).toBeLessThan(MAX_FRAME_BYTES);
    const blobs=await ScopedProtocolBlobs.create(root,input.scope,input.epoch);
    const runReference=await blobs.encode(input);const resultReference=await blobs.encode(response);
    child=Bun.spawn([process.execPath,source],{stdin:"pipe",stdout:"pipe",stderr:"pipe"});
    const runningChild=child;
    const stderr=new Response(runningChild.stderr as ReadableStream<Uint8Array>).text();
    (runningChild.stdin as Bun.FileSink).write(encodeFrame({type:"run_turn",protocol:1,scope:input.scope,epoch:input.epoch,blob_dir:root,pi_payload:runReference}));
    const observed:Record<string,unknown>[]=[];
    const drain=(async()=>{
      let buffer="";
      for await(const chunk of runningChild.stdout as ReadableStream<Uint8Array>) {
        buffer+=new TextDecoder().decode(chunk);let lf:number;
        while((lf=buffer.indexOf("\n"))>=0) {
          const raw=buffer.slice(0,lf);buffer=buffer.slice(lf+1);
          expect(Buffer.byteLength(raw)).toBeLessThan(INLINE_LIMIT_BYTES);
          const event=losslessParse(raw) as Record<string,unknown>;observed.push(event);
          if(event.type==="operation") (runningChild.stdin as Bun.FileSink).write(encodeFrame({type:"operation_result",pi_payload:resultReference}));
        }
      }
    })();
    expect(await within(runningChild.exited,30000)).toBe(0);await drain;
    expect(observed.map(event=>event.type)).toEqual(["operation","assistant_ready","error"]);
    expect(observed.at(-1)).toEqual({type:"error",code:"frame_too_large",message:"Protocol frame exceeds its limit"});
    expect(await stderr).toBe("");
  } finally {
    if(child && child.exitCode===null){child.kill();await child.exited;}
    const absolute=resolve(root);
    if(!absolute.startsWith(resolve(temp)+sep))throw new Error("Oversized test cleanup path escaped its temporary directory");
    await rm(absolute,{recursive:true,force:true});
  }
},40000);
