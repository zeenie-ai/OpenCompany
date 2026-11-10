import {copyFile,readdir,readFile,rm,writeFile} from "node:fs/promises";
import {dirname,join,resolve,sep} from "node:path";
import {fileURLToPath} from "node:url";
import {SDK_VERSION} from "../src/types.ts";

const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const dist=join(root,"dist");
const sdkManifest=join(root,"node_modules","@earendil-works","pi-coding-agent","package.json");
if (JSON.parse(await readFile(sdkManifest,"utf8")).version!==SDK_VERSION) throw new Error("Pi distribution SDK version differs from its pin");

// One file. The SDK and every package it imports are inlined, so the host needs
// no node_modules and nothing is installed or unpacked where it runs. dist is
// rebuilt whole: files left by an earlier build must never ship.
await rm(dist,{recursive:true,force:true});
const result=await Bun.build({entrypoints:[join(root,"src","index.ts")],outdir:dist,target:"bun",minify:false,metafile:true});
if (!result.success) throw new Error(`Pi host build failed: ${result.logs.join("\n")}`);
// The SDK reads its own name and version from the package.json in PI_PACKAGE_DIR.
await copyFile(sdkManifest,join(dist,"package.json"));

// The inlined packages' licenses travel with the bundle that now contains them.
const marker=`${sep}node_modules${sep}`;
const packages=new Map<string,{name?:string;version?:string;license?:unknown}>();
for (const input of Object.keys(result.metafile!.inputs)) {
  const path=resolve(input);
  const at=path.lastIndexOf(marker);
  if (at<0) continue;
  const [scope,name]=path.slice(at+marker.length).split(sep);
  const directory=join(path.slice(0,at+marker.length),scope!.startsWith("@") ? join(scope!,name!) : scope!);
  if (!packages.has(directory)) packages.set(directory,JSON.parse(await readFile(join(directory,"package.json"),"utf8")));
}
const notices:string[]=[];
for (const [directory,manifest] of [...packages].sort(([,a],[,b])=>String(a.name).localeCompare(String(b.name)))) {
  const files=(await readdir(directory,{withFileTypes:true})).filter(entry=>entry.isFile() && /^(licen[sc]e|copying|notice)/i.test(entry.name)).map(entry=>entry.name).sort();
  const texts=await Promise.all(files.map(file=>readFile(join(directory,file),"utf8")));
  notices.push([`${manifest.name} ${manifest.version}`,typeof manifest.license==="string" ? `License: ${manifest.license}` : "",...texts.map(text=>text.trim())].filter(Boolean).join("\n\n"));
}
await writeFile(join(dist,"THIRD-PARTY-LICENSES.txt"),notices.join(`\n\n${"-".repeat(72)}\n\n`)+"\n");
console.log(`Built the Pi host bundle (${Math.ceil(result.outputs[0]!.size/1024/1024)} MiB, ${packages.size} packages inlined)`);
