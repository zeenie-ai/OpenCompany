import {afterAll,beforeAll,describe,test} from "bun:test";
import {copyFile,mkdtemp,realpath,rm} from "node:fs/promises";
import {dirname,join,resolve,sep} from "node:path";
import {tmpdir} from "node:os";
import {fileURLToPath} from "node:url";
import {smokeHost} from "./process-helpers.ts";

const dist=resolve(dirname(fileURLToPath(import.meta.url)),"..","dist");
describe.skipIf(!await Bun.file(join(dist,"package.json")).exists())("production bundle",()=>{
  let scratch:string;let isolated:string|undefined;
  beforeAll(async()=>{
    // Outside the repository, so a missing package cannot resolve from
    // server/pi/node_modules, root node_modules or any development workspace.
    scratch=await realpath(tmpdir());
    isolated=await realpath(await mkdtemp(join(scratch,"opencompany-pi-offline-")));
    for (const file of ["index.js","package.json"]) await copyFile(join(dist,file),join(isolated,file));
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
});
