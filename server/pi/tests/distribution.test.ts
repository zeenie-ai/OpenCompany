import {afterAll,beforeAll,describe,expect,test} from "bun:test";
import {copyFile,mkdtemp,readFile,realpath,rm} from "node:fs/promises";
import {dirname,join,resolve,sep} from "node:path";
import {tmpdir} from "node:os";
import {fileURLToPath} from "node:url";
import {smokeHost,within} from "./process-helpers.ts";

const dist=resolve(dirname(fileURLToPath(import.meta.url)),"..","dist");
describe.skipIf(!await Bun.file(join(dist,"package.json")).exists())("production bundle",()=>{
  let scratch:string;let isolated:string|undefined;
  beforeAll(async()=>{
    // Outside the repository, so a missing package cannot resolve from
    // server/pi/node_modules, root node_modules or any development workspace.
    scratch=await realpath(tmpdir());
    isolated=await realpath(await mkdtemp(join(scratch,"opencompany-pi-offline-")));
    for (const file of ["index.js","tool-host.js","package.json"]) await copyFile(join(dist,file),join(isolated,file));
  });
  afterAll(async()=>{
    if(!isolated)return;
    if (!resolve(isolated).startsWith(resolve(scratch)+sep)) throw new Error("Invalid offline test scratch path");
    await rm(isolated,{recursive:true,force:true,maxRetries:3,retryDelay:100});
  });
  // The budget only catches a hang. A cold start on a loaded Windows runner has
  // taken many times the usual second.
  test("runs offline from one file, with no dependency tree beside it",async()=>{
    if(!isolated)throw new Error("The bundle was not copied");
    await smokeHost(join(isolated,"index.js"),{NODE_PATH:"",PI_PACKAGE_DIR:isolated,PI_OFFLINE:"1"},60000);
  },70000);
  test("tool bundle reads and writes offline outside the dependency tree",async()=>{
    if(!isolated)throw new Error("The bundle was not copied");
    const invoke=async(tool:string,args:Record<string,unknown>)=>{
      const child=Bun.spawn([process.execPath,join(isolated!,"tool-host.js")],{
        stdin:"pipe",stdout:"pipe",stderr:"pipe",
        env:{...process.env,NODE_PATH:"",PI_PACKAGE_DIR:isolated!,PI_OFFLINE:"1"},
      });
      const stdout=new Response(child.stdout).text();
      const stderr=new Response(child.stderr).text();
      try {
        child.stdin.write(JSON.stringify({protocol:1,tool,cwd:isolated,args})+"\n");
        const [exitCode,out,err]=await within(Promise.all([child.exited,stdout,stderr]),60000,"Pi built-in bundled tool");
        expect(exitCode).toBe(0);expect(err).toBe("");
        expect(out.trim().split("\n")).toHaveLength(1);
        const response=JSON.parse(out);
        expect(response.ok).toBe(true);
        return response.result;
      } finally {
        if(child.exitCode===null)child.kill("SIGKILL");
        await within(Promise.allSettled([child.exited,stdout,stderr]),5000,"Pi built-in smoke cleanup");
      }
    };
    await invoke("write",{path:"offline.txt",content:"bundled tool works"});
    expect(await readFile(join(isolated,"offline.txt"),"utf8")).toBe("bundled tool works");
    const result=await invoke("read",{path:"offline.txt"});
    expect(result.content).toEqual([{type:"text",text:"bundled tool works"}]);
  },130000);
});
