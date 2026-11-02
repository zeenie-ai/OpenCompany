import {createHash} from "node:crypto";
import {mkdir,mkdtemp,copyFile,readFile,writeFile,rm,rename,readdir} from "node:fs/promises";
import {dirname,join,resolve,sep} from "node:path";
import {fileURLToPath} from "node:url";
import {tarBinary} from "./tar.ts";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const dist=join(root,"dist");
await mkdir(dist,{recursive:true});
const result=await Bun.build({entrypoints:[join(root,"src","index.ts")],outdir:dist,target:"bun",packages:"external",minify:false});
if (!result.success) throw new Error(`Pi host build failed: ${result.logs.join("\n")}`);
const buildRoot=join(root,".runtime-build");await mkdir(buildRoot,{recursive:true});
const runtime=await mkdtemp(join(buildRoot,"runtime-"));
try {
  await copyFile(join(root,"package.json"),join(runtime,"package.json"));
  await copyFile(join(root,"bun.lock"),join(runtime,"bun.lock"));
  const install=Bun.spawn([process.execPath,"install","--production","--frozen-lockfile","--linker","hoisted","--os","*","--cpu","*"],{cwd:runtime,stdin:"ignore",stdout:"inherit",stderr:"inherit"});
  if (await install.exited!==0) throw new Error("Frozen Pi production dependency installation failed");
  await copyFile(join(dist,"index.js"),join(runtime,"index.js"));
  const sdk=JSON.parse(await readFile(join(runtime,"node_modules","@earendil-works","pi-coding-agent","package.json"),"utf8"));
  if (sdk.version!=="1.1.0") throw new Error("Pi distribution SDK version differs from its pin");
  const archive=join(runtime,"runtime.tgz");
  let tar:ReturnType<typeof Bun.spawn>;
  try {tar=Bun.spawn([tarBinary(),"-czf",archive,"-C",runtime,"index.js","package.json","node_modules"],{stdout:"inherit",stderr:"inherit"});}
  catch {throw new Error("Building the Pi runtime requires tar (Windows bsdtar or Unix tar)");}
  if (await tar.exited!==0) throw new Error("Pi runtime archive creation failed");
  const bytes=await readFile(archive);
  const executables=(await readdir(join(runtime,"node_modules","@esbuild"))).filter(name=>!name.startsWith("win32-")).map(name=>`node_modules/@esbuild/${name}/bin/esbuild`);
  await writeFile(join(runtime,"runtime-manifest.json"),JSON.stringify({protocol:1,sdk_version:"1.1.0",archive:"runtime.tgz",
    sha256:createHash("sha256").update(bytes).digest("hex"),bytes:bytes.length,entrypoint:"index.js",
    sdk_package_dir:"node_modules/@earendil-works/pi-coding-agent",universal:true,executables},null,2)+"\n");
  // Publish finished assets only. A failed install/archive leaves the last distribution intact.
  await rename(archive,join(dist,"runtime.tgz"));
  await rename(join(runtime,"runtime-manifest.json"),join(dist,"runtime-manifest.json"));
  console.log(`Built pinned Pi runtime (${Math.ceil(bytes.length/1024/1024)} MiB)`);
} finally {
  // Remove only this exact temporary child of the private build directory.
  if (!resolve(runtime).startsWith(resolve(buildRoot)+sep)) throw new Error("Invalid Pi build scratch path");
  await rm(runtime,{recursive:true,force:true});
}
