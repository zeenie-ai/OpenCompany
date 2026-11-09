import {expect} from "bun:test";
import {encodeFrame} from "../src/protocol.ts";
import {message,run,toolResult,call} from "./fixtures.ts";

export async function within<T>(promise:Promise<T>,milliseconds:number,phase:string):Promise<T> {
  let timer:ReturnType<typeof setTimeout>|undefined;
  try {return await Promise.race([promise,new Promise<never>((_resolve,reject)=>{
    timer=setTimeout(()=>reject(new Error(`${phase} exceeded ${milliseconds} ms`)),milliseconds);
  })]);} finally {if(timer)clearTimeout(timer);}
}

/** Drain both pipes immediately: awaiting exit first can block a verbose child. */
export async function captureProcess(command:string[],phase:string,milliseconds:number):Promise<{exitCode:number;stdout:string;stderr:string}> {
  const child=Bun.spawn(command,{stdin:"ignore",stdout:"pipe",stderr:"pipe"});
  let stdout="";let stderr="";
  const drain=async(stream:ReadableStream<Uint8Array>,append:(text:string)=>void)=>{
    const decoder=new TextDecoder();
    for await(const chunk of stream) append(decoder.decode(chunk,{stream:true}));
    append(decoder.decode());
  };
  const drains=Promise.all([drain(child.stdout,text=>{stdout+=text;}),drain(child.stderr,text=>{stderr+=text;})]);
  try {
    const [exitCode]=await within(Promise.all([child.exited,drains]),milliseconds,phase);
    return {exitCode,stdout,stderr};
  } catch(error) {
    throw new Error(`${phase}: ${error instanceof Error ? error.message : String(error)}; stderr: ${stderr.slice(-2000)}`);
  } finally {
    if(child.exitCode===null)child.kill("SIGKILL");
    await within(Promise.allSettled([child.exited,drains]),5000,`${phase} process cleanup`);
  }
}

export async function smokeHost(entrypoint:string,env:Record<string,string|undefined>={},milliseconds=10000):Promise<void> {
  const child=Bun.spawn([process.execPath,entrypoint],{stdin:"pipe",stdout:"pipe",stderr:"pipe",env:{...process.env,...env}});
  let buffer="";let ended=false;let stderr="";const observed:string[]=[];
  const stderrDrain=(async()=>{const decoder=new TextDecoder();for await(const chunk of child.stderr)stderr+=decoder.decode(chunk,{stream:true});stderr+=decoder.decode();})();
  const stdoutDrain=(async()=>{
    const decoder=new TextDecoder();
    for await(const chunk of child.stdout) {
      buffer+=decoder.decode(chunk,{stream:true});let lf:number;
      while((lf=buffer.indexOf("\n"))>=0) {
        const event=JSON.parse(buffer.slice(0,lf));buffer=buffer.slice(lf+1);observed.push(event.type);
        if(event.type==="operation") child.stdin.write(encodeFrame({type:"operation_result",id:event.id,
          result:event.kind==="model"?{assistant_message:message("assistant","read",[call()])}:{message:toolResult()}}));
        if(event.type==="turn_checkpoint") {expect(event.snapshot.entries.some((entry:{type:string;message?:{role:string}})=>entry.type==="message" && entry.message?.role==="toolResult")).toBe(true);ended=true;}
        if(event.type==="error")throw new Error(`${event.code}: ${event.message}`);
      }
    }
  })();
  const drains=Promise.all([stdoutDrain,stderrDrain]);
  try {
    child.stdin.write(encodeFrame(run()));
    const [exitCode]=await within(Promise.all([child.exited,drains]),milliseconds,"Pi host checkpoint and exit");
    expect(ended).toBe(true);expect(observed).toEqual(["operation","assistant_ready","operation","turn_checkpoint"]);
    expect(exitCode).toBe(0);expect(stderr).toBe("");
  } catch(error) {
    throw new Error(`${error instanceof Error ? error.message : String(error)}; observed frames: ${observed.join(",") || "none"}; stderr: ${stderr.slice(-2000)}`);
  } finally {
    if(child.exitCode===null)child.kill("SIGKILL");
    await within(Promise.allSettled([child.exited,drains]),5000,"Pi host process cleanup");
  }
}
