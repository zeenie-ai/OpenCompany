/** One Pi built-in invocation. The Temporal activity owns this process lifetime. */
import {mkdtemp, rm} from "node:fs/promises";
import {join, resolve, sep} from "node:path";
import {tmpdir} from "node:os";

process.env.PI_OFFLINE = "1";
const temporaryRoot = resolve(tmpdir());
const privateAgentDir = await mkdtemp(join(temporaryRoot, "opencompany-pi-tool-"));
process.env.PI_CODING_AGENT_DIR = privateAgentDir;
const {BUILTIN_FRAME_LIMIT, executeBuiltinTool} = await import("./builtin-tools.ts");
const controller = new AbortController();
let task: Promise<void> | undefined;
let ended = false;
let failure: Error | undefined;
let buffer = Buffer.alloc(0);

function abort(): void {controller.abort();}
process.once("SIGTERM", abort);
process.once("SIGINT", abort);

async function respond(value: unknown): Promise<void> {
  if (ended) return;
  ended = true;
  let encoded = JSON.stringify(value) + "\n";
  if (Buffer.byteLength(encoded) > BUILTIN_FRAME_LIMIT) encoded = JSON.stringify({ok: false, error: "Pi tool result exceeds the 16 MiB transport limit. Read a smaller range."}) + "\n";
  await new Promise<void>((resolveWrite, reject) => {process.stdout.write(encoded, error => error ? reject(error) : resolveWrite());});
  process.stdin.destroy();
}

function start(request: unknown): void {
  task = (async () => {
    try {
      const result = await executeBuiltinTool(request, controller.signal);
      await respond(failure ? {ok: false, error: failure.message} : {ok: true, result});
    } catch (error) {
      const message = failure?.message ?? (error instanceof Error ? error.message : "Pi tool failed");
      // SDK timeout errors end with the status after retained command output.
      // Keep that tail when bounding the diagnostic sent to the parent.
      const bounded = message.length > 16000 ? `${message.slice(0, 4000)}\n[Output omitted]\n${message.slice(-11000)}` : message;
      await respond({ok: false, error: bounded});
    }
  })();
}

try {
  for await (const chunk of process.stdin) {
    buffer = Buffer.concat([buffer, chunk]);
    if (buffer.length > BUILTIN_FRAME_LIMIT) throw new Error("Pi tool request exceeds the 16 MiB transport limit.");
    let lf: number;
    while ((lf = buffer.indexOf(10)) >= 0) {
      const frame = JSON.parse(buffer.subarray(0, lf).toString("utf8"));
      buffer = buffer.subarray(lf + 1);
      if (!task) start(frame);
      else if (frame?.type === "abort") abort();
      else throw new Error("A Pi tool host accepts one request followed only by abort.");
    }
  }
  if (!ended) {
    if (buffer.length) failure = new Error("Incomplete Pi tool request.");
    else if (!task) failure = new Error("No Pi tool request received.");
    abort();
  }
} catch (error) {
  if (!ended) {failure = error instanceof Error ? error : new Error("Pi tool transport failed."); abort();}
} finally {
  await task;
  if (!ended) await respond({ok: false, error: failure?.message ?? "Pi tool aborted."});
  process.removeListener("SIGTERM", abort);
  process.removeListener("SIGINT", abort);
  if (!resolve(privateAgentDir).startsWith(temporaryRoot + sep)) throw new Error("Invalid Pi tool scratch directory.");
  await rm(privateAgentDir, {recursive: true, force: true});
}
