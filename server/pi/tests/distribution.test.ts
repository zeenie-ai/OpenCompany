import {afterAll,beforeAll,describe,expect,test} from "bun:test";
import {createHash} from "node:crypto";
import {readFile,mkdtemp,realpath,rm} from "node:fs/promises";
import {dirname,join,resolve,sep} from "node:path";
import {tmpdir} from "node:os";
import {fileURLToPath} from "node:url";
import {captureProcess,smokeHost,within} from "./process-helpers.ts";
import {tarBinary} from "../scripts/tar.ts";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const manifestPath=join(root,"dist","runtime-manifest.json");
describe.skipIf(!await Bun.file(manifestPath).exists())("production archive",()=>{
  let manifest:{archive:string;sha256:string;sdk_version:string;universal:boolean;entrypoint:string;sdk_package_dir:string};
  let buildRoot:string;let isolated:string|undefined;
  // Cold Windows extraction and removal can dominate this large dependency
  // tree. Keep fixture work separate from the bounded host startup contract.
  beforeAll(async()=>{
    manifest=JSON.parse(await readFile(manifestPath,"utf8"));
    const archive=join(root,"dist",manifest.archive);
    const bytes=await readFile(archive);
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(manifest.sha256);
    expect(manifest.sdk_version).toBe("1.1.0");
    expect(manifest.universal).toBe(true);
    // Outside the repository so missing runtime packages cannot resolve from
    // server/pi/node_modules, root node_modules or any development workspace.
    buildRoot=await realpath(tmpdir());
    isolated=await realpath(await mkdtemp(join(buildRoot,"opencompany-pi-offline-")));
    const unpack=await captureProcess([tarBinary(),"-xzf",archive,"-C",isolated],"Offline runtime archive extraction",25000);
    expect(unpack.exitCode,`Archive extraction failed: ${unpack.stderr}`).toBe(0);
    for (const platform of ["darwin-arm64","darwin-x64","linux-x64","win32-x64"]) {
      const binary=join(isolated,"node_modules","@esbuild",platform,platform.startsWith("win32-") ? "esbuild.exe" : "bin/esbuild");
      expect(await Bun.file(binary).exists()).toBe(true);
    }
  },30000);
  afterAll(async()=>{
    if(!isolated)return;
    if (!resolve(isolated).startsWith(resolve(buildRoot)+sep)) throw new Error("Invalid offline test scratch path");
    await within(rm(isolated,{recursive:true,force:true,maxRetries:3,retryDelay:100}),25000,"Offline runtime scratch cleanup");
  },30000);
  test("runs offline without source or root dependency resolution",async()=>{
    if(!isolated)throw new Error("Offline runtime extraction did not create a fixture");
    await smokeHost(join(isolated,manifest.entrypoint),{NODE_PATH:"",PI_PACKAGE_DIR:join(isolated,manifest.sdk_package_dir),PI_OFFLINE:"1"});
  },15000);
});
