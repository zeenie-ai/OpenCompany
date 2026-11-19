/** Execute Pi's actual tools inside a durable OpenCompany leaf, not the model driver. */
import {constants} from "node:fs";
import {access, copyFile, lstat, mkdir, open, readdir, realpath, rename, stat, unlink} from "node:fs/promises";
import {randomUUID} from "node:crypto";
import {basename, dirname, isAbsolute, join, relative, resolve, sep} from "node:path";
import {tmpdir} from "node:os";
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";

export const BUILTIN_FRAME_LIMIT = 16 * 1024 * 1024;
export const BUILTIN_NAMES = ["read", "write", "edit", "bash", "powershell", "grep", "find", "ls"] as const;
export type BuiltinName = typeof BUILTIN_NAMES[number];
export interface BuiltinRequest {protocol: 1; tool: BuiltinName; cwd: string; args: Record<string, unknown>}
type SDK = typeof import("@earendil-works/pi-coding-agent");
let sdkPromise: Promise<SDK> | undefined;

function sdk(): Promise<SDK> {
  process.env.PI_OFFLINE = "1";
  return sdkPromise ??= import("@earendil-works/pi-coding-agent");
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function validateBuiltinRequest(value: unknown): BuiltinRequest {
  if (!isObject(value) || value.protocol !== 1 || !BUILTIN_NAMES.includes(value.tool as BuiltinName)
      || typeof value.cwd !== "string" || !isAbsolute(value.cwd) || !isObject(value.args)) {
    throw new Error("Invalid Pi tool request: protocol, tool, absolute workspace and arguments are required.");
  }
  const args = {...value.args};
  const name = value.tool as BuiltinName;
  for (const key of name === "edit" || name === "write" || name === "read" ? ["path"]
    : name === "bash" || name === "powershell" ? ["command"] : name === "find" || name === "grep" ? ["pattern"] : []) {
    if (typeof args[key] !== "string" || !args[key]) throw new Error(`${key} must be a non-empty string.`);
  }
  if (name === "write" && typeof args.content !== "string") throw new Error("content must be a string.");
  if (name === "edit" && (!Array.isArray(args.edits) || !args.edits.length || !args.edits.every(edit =>
    isObject(edit) && typeof edit.oldText === "string" && !!edit.oldText && typeof edit.newText === "string"))) {
    throw new Error("edits must contain oldText/newText string pairs with non-empty oldText.");
  }
  for (const key of ["path", "glob"]) if (args[key] !== undefined && typeof args[key] !== "string") throw new Error(`${key} must be a string.`);
  for (const key of ["offset", "limit", "context"]) {
    if (args[key] !== undefined && (typeof args[key] !== "number" || !Number.isSafeInteger(args[key]) || (args[key] as number) < (key === "context" ? 0 : 1))) {
      throw new Error(`${key} must be a ${key === "context" ? "non-negative" : "positive"} integer.`);
    }
  }
  for (const key of ["ignoreCase", "literal"]) if (args[key] !== undefined && typeof args[key] !== "boolean") throw new Error(`${key} must be a boolean.`);
  if (name === "bash" || name === "powershell") {
    args.timeout ??= 30;
    if (typeof args.timeout !== "number" || !Number.isFinite(args.timeout) || args.timeout < 1 || args.timeout > 600) {
      throw new Error("Shell timeout must be between 1 and 600 seconds.");
    }
  }
  return {protocol: 1, tool: name, cwd: value.cwd, args};
}

/** Every final filesystem operation revalidates the path; cwd by itself is not confinement. */
class Workspace {
  constructor(readonly root: string, readonly signal: AbortSignal) {}
  checkAbort(): void {if (this.signal.aborted) throw new Error("Operation aborted");}
  async checked(path: string, allowMissing = false): Promise<string> {
    this.checkAbort();
    if (path.includes("\0")) throw new Error("Invalid workspace path.");
    if ((await lstat(this.root)).isSymbolicLink()) throw new Error("Workflow workspace changed to a symbolic link.");
    const absolute = resolve(this.root, path);
    const suffix = relative(this.root, absolute);
    if (suffix === ".." || suffix.startsWith(`..${sep}`) || isAbsolute(suffix)) throw new Error("Path is outside the workflow workspace.");
    // Disallow symlinks and Windows junctions in filesystem tools. The shell is an
    // explicitly open-world capability and does not claim this restriction.
    let cursor = this.root;
    for (const part of suffix.split(sep).filter(Boolean)) {
      if (process.platform === "win32" && part.includes(":")) throw new Error("Alternate data streams are not supported.");
      cursor = join(cursor, part);
      try {
        if ((await lstat(cursor)).isSymbolicLink()) throw new Error("Symbolic links are not supported by Pi workspace file tools.");
      } catch (error) {
        if (allowMissing && (error as NodeJS.ErrnoException).code === "ENOENT") break;
        throw error;
      }
    }
    this.checkAbort();
    return absolute;
  }
  async read(path: string): Promise<Buffer> {
    const checked = await this.checked(path);
    const file = await open(checked, constants.O_RDONLY | (process.platform === "win32" ? 0 : constants.O_NOFOLLOW));
    try {
      await this.checked(checked);
      const info = await file.stat();
      const current = await lstat(checked);
      if (!info.isFile() || info.dev !== current.dev || info.ino !== current.ino) throw new Error("Workspace file changed while opening it.");
      return await file.readFile();
    } finally {await file.close();}
  }
  async write(path: string, content: string): Promise<void> {
    const checked = await this.checked(path, true);
    const parent = await this.checked(dirname(checked));
    const parentBefore = await stat(parent);
    const temporary = join(parent, `.opencompany-pi-${randomUUID()}.tmp`);
    const file = await open(temporary, "wx");
    try {
      await file.writeFile(content, "utf8");
      await file.sync();
      await file.close();
      await this.checked(checked, true);
      const parentAfter = await stat(parent);
      if (parentBefore.dev !== parentAfter.dev || parentBefore.ino !== parentAfter.ino) throw new Error("Workspace parent directory changed during write.");
      this.checkAbort();
      await rename(temporary, checked);
    } finally {
      await file.close().catch(() => {});
      await unlink(temporary).catch(() => {});
    }
  }
  async exists(path: string): Promise<boolean> {
    try {await this.checked(path); return true;}
    catch (error) {if ((error as NodeJS.ErrnoException).code === "ENOENT") return false; throw error;}
  }
}

/** Supply Pi find's glob operation with the same installed ripgrep used by Pi grep. */
async function findFiles(workspace: Workspace, pattern: string, path: string, limit: number): Promise<string[]> {
  const cwd = await workspace.checked(path);
  const rg = Bun.which("rg");
  if (!rg) throw new Error("Pi Find Files requires ripgrep (rg) on PATH. Automatic downloads are disabled.");
  // rg handles nested .gitignore files and repo boundaries. Outside a repository,
  // --no-require-git makes local .gitignore files meaningful, as Pi's fd path does.
  let inRepository = false;
  for (let current = cwd;; current = dirname(current)) {
    try {await lstat(join(current, ".git")); inRepository = true; break;} catch (error) {if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;}
    if (current === dirname(current)) break;
  }
  // A positive rg --glob overrides .gitignore. Enumerate the ignored-aware
  // file set first, then match the requested glob ourselves.
  const matcher = new Bun.Glob(pattern);
  const args = ["--files", "--hidden", ...(inRepository ? [] : ["--no-require-git"]),
    "--glob", "!**/node_modules/**", "--glob", "!**/.git/**", "--", "."];
  workspace.checkAbort();
  const child = spawn(rg, args, {cwd, stdio: ["ignore", "pipe", "pipe"], windowsHide: true});
  const lines = createInterface({input: child.stdout});
  const matches: string[] = [];
  let stderr = "";
  let limited = false;
  const abort = () => child.kill();
  workspace.signal.addEventListener("abort", abort, {once: true});
  child.stderr.on("data", chunk => {stderr = (stderr + chunk.toString()).slice(-4096);});
  lines.on("line", line => {
    const candidate = relative(cwd, resolve(cwd, line)).split(sep).join("/");
    if (matches.length < limit && matcher.match(pattern.includes("/") ? candidate : basename(candidate))) matches.push(resolve(cwd, line));
    if (matches.length >= limit) {limited = true; child.kill();}
  });
  try {
    const code = await new Promise<number | null>((resolveExit, reject) => {child.once("error", reject); child.once("close", resolveExit);});
    workspace.checkAbort();
    if (code !== 0 && code !== 1 && !limited) throw new Error(`Pi Find Files failed: ${stderr || `ripgrep exit ${code}`}`);
    const safe: string[] = [];
    for (const match of matches) {
      try {safe.push(await workspace.checked(match));} catch (error) {if (workspace.signal.aborted) throw error;}
    }
    return safe;
  } finally {lines.close(); workspace.signal.removeEventListener("abort", abort); if (child.exitCode === null) child.kill();}
}

/** Move SDK overflow output into the workspace so Pi Read File can retrieve it. */
async function persistShellOutput(root: string, source: string): Promise<string> {
  const temporaryRoot = await realpath(tmpdir());
  const original = await realpath(source);
  const sourceInfo = await lstat(source);
  if (!sourceInfo.isFile() || sourceInfo.isSymbolicLink() || dirname(original) !== temporaryRoot
      || !/^pi-(bash|powershell)-[a-f0-9]{16}\.log$/.test(basename(original))) {
    throw new Error("Pi returned an invalid shell output file.");
  }
  // Output is useful after timeout/abort too. This finishes before the activity
  // releases its result, independently of the execution's aborted signal.
  const workspace = new Workspace(root, new AbortController().signal);
  const directory = await workspace.checked(join(root, ".pi-output"), true);
  await mkdir(directory, {recursive: true});
  await workspace.checked(directory);
  const destination = await workspace.checked(join(directory, basename(original)), true);
  await copyFile(original, destination, constants.COPYFILE_EXCL);
  await workspace.checked(destination);
  await unlink(original);
  return relative(root, destination).split(sep).join("/");
}

function replaceOutputPath(value: unknown, original: string, replacement: string): unknown {
  if (typeof value === "string") return value.replaceAll(original, replacement);
  if (Array.isArray(value)) return value.map(item => replaceOutputPath(item, original, replacement));
  if (isObject(value)) return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, replaceOutputPath(item, original, replacement)]));
  return value;
}

export async function executeBuiltinTool(input: unknown, signal: AbortSignal): Promise<unknown> {
  const request = validateBuiltinRequest(input);
  const root = await realpath(request.cwd);
  if (!(await stat(root)).isDirectory()) throw new Error("Pi tool workspace must be a directory.");
  const workspace = new Workspace(root, signal);
  const pi = await sdk();
  const args = {...request.args};
  if (request.tool !== "bash" && request.tool !== "powershell") {
    args.path = await workspace.checked(String(args.path || "."), request.tool === "write");
  }
  const readOperations = {
    readFile: (path: string) => workspace.read(path),
    access: async (path: string) => {await access(await workspace.checked(path), constants.R_OK);},
    detectImageMimeType: async (path: string) => pi.detectSupportedImageMimeTypeFromFile(await workspace.checked(path)),
  };
  const tools = {
    read: () => pi.createReadTool(root, {autoResizeImages: false, operations: readOperations}),
    write: () => pi.createWriteTool(root, {operations: {
      writeFile: (path: string, content: string) => workspace.write(path, content),
      mkdir: async (path: string) => {await mkdir(await workspace.checked(path, true), {recursive: true}); await workspace.checked(path);},
    }}),
    edit: () => pi.createEditTool(root, {operations: {
      readFile: (path: string) => workspace.read(path), writeFile: (path: string, content: string) => workspace.write(path, content),
      access: async (path: string) => {await access(await workspace.checked(path), constants.R_OK | constants.W_OK);},
    }}),
    bash: () => pi.createBashTool(root, {exposeSessionEnvironment: false}),
    powershell: () => pi.createPowerShellTool(root, {exposeSessionEnvironment: false}),
    grep: () => pi.createGrepTool(root, {operations: {
      isDirectory: async (path: string) => (await stat(await workspace.checked(path))).isDirectory(),
      readFile: async (path: string) => (await workspace.read(path)).toString("utf8"),
    }}),
    find: () => pi.createFindTool(root, {operations: {exists: (path: string) => workspace.exists(path),
      glob: (pattern: string, path: string, options: {limit: number}) => findFiles(workspace, pattern, path, options.limit)}}),
    ls: () => pi.createLsTool(root, {operations: {exists: (path: string) => workspace.exists(path),
      stat: async (path: string) => stat(await workspace.checked(path)), readdir: async (path: string) => readdir(await workspace.checked(path))}}),
  };
  if (request.tool === "grep" && !Bun.which("rg")) throw new Error("Pi Search Text requires ripgrep (rg) on PATH. Automatic downloads are disabled.");
  const tool = tools[request.tool]();
  // The parent validates the canonical node schema; validateBuiltinRequest also
  // protects direct protocol callers. SDK wrappers do not coerce these args.
  let overflowPath: string | undefined;
  let result: unknown;
  try {
    result = await tool.execute("opencompany-leaf", args as never, signal, update => {
      const path = isObject(update.details) ? update.details.fullOutputPath : undefined;
      if (typeof path === "string") overflowPath = path;
    });
  } catch (error) {
    if (overflowPath) {
      const stored = await persistShellOutput(root, overflowPath);
      if (error instanceof Error) error.message = error.message.replaceAll(overflowPath, stored);
    }
    throw error;
  }
  if (!overflowPath) return result;
  const stored = await persistShellOutput(root, overflowPath);
  return replaceOutputPath(result, overflowPath, stored);
}
