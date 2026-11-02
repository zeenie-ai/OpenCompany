import {existsSync} from "node:fs";
import {join} from "node:path";

/** Windows' own bsdtar: under Git Bash, as in CI, PATH's tar is GNU tar,
 * which reads "D:\..." as a remote host. Same rule as desktop/scripts/_lib.ts. */
export function tarBinary():string {
  if (process.platform==="win32") {
    const system=join(process.env.SystemRoot??"C:\\Windows","System32","tar.exe");
    if (existsSync(system)) return system;
  }
  return "tar";
}
