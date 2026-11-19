import {afterAll, beforeAll, describe, expect, test} from "bun:test";
import {mkdir, readFile, realpath, rm, symlink, writeFile} from "node:fs/promises";
import {randomUUID} from "node:crypto";
import {join, resolve, sep} from "node:path";
import {executeBuiltinTool, validateBuiltinRequest, type BuiltinName} from "../src/builtin-tools.ts";

type Result = {content: Array<{type: string; text?: string; data?: string; mimeType?: string}>;
  details?: Record<string, unknown>; structuredContent?: Record<string, unknown> | string; isError?: boolean};
const testRoot = resolve(process.cwd());
const scratch = join(testRoot, `.builtin-tools-test-${randomUUID()}`);
let workspace: string;
const call = async (tool: BuiltinName, args: Record<string, unknown>, signal = new AbortController().signal) =>
  await executeBuiltinTool({protocol: 1, tool, cwd: workspace, args}, signal) as Result;
const text = (result: Result) => result.content.filter(block => block.type === "text").map(block => block.text).join("\n");

beforeAll(async () => {
  await mkdir(join(scratch, "workspace"), {recursive: true});
  workspace = await realpath(join(scratch, "workspace"));
  process.env.PI_OFFLINE = "1";
});
afterAll(async () => {
  if (!resolve(scratch).startsWith(testRoot + sep)) throw new Error("Invalid test scratch path");
  await rm(scratch, {recursive: true, force: true, maxRetries: 3});
});

describe("Pi built-in leaf tools", () => {
  test("uses real write, paginated read and multi-edit with SDK diffs", async () => {
    await call("write", {path: "nested/source.txt", content: "alpha\nbeta\ngamma\n"});
    expect(await readFile(join(workspace, "nested/source.txt"), "utf8")).toBe("alpha\nbeta\ngamma\n");
    const read = await call("read", {path: "nested/source.txt", offset: 2, limit: 1});
    expect(text(read)).toStartWith("beta\n");
    const edit = await call("edit", {path: "nested/source.txt", edits: [{oldText: "alpha", newText: "one"}, {oldText: "gamma", newText: "three"}]});
    expect(edit.details?.diff).toBeString();
    expect(edit.details?.patch).toBeString();
    expect(await readFile(join(workspace, "nested/source.txt"), "utf8")).toBe("one\nbeta\nthree\n");
  });
  test("rejects an ambiguous edit without changing the file", async () => {
    await writeFile(join(workspace, "ambiguous.txt"), "duplicate\nduplicate\n");
    await expect(call("edit", {path: "ambiguous.txt", edits: [{oldText: "duplicate", newText: "changed"}]})).rejects.toThrow();
    expect(await readFile(join(workspace, "ambiguous.txt"), "utf8")).toBe("duplicate\nduplicate\n");
  });
  test("keeps image content without requiring resizing WASM", async () => {
    const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=";
    await writeFile(join(workspace, "pixel.png"), Buffer.from(png, "base64"));
    const result = await call("read", {path: "pixel.png"});
    expect(result.content.find(block => block.type === "image")).toEqual({type: "image", mimeType: "image/png", data: png});
  });
  test("lists dotfiles and directories through SDK formatting", async () => {
    await writeFile(join(workspace, ".visible"), "yes");
    const listing = text(await call("ls", {}));
    expect(listing).toContain(".visible");
    expect(listing).toContain("nested/");
  });
  test("rejects traversal, external absolute paths and symlink aliases for reads and writes", async () => {
    const outside = join(scratch, "outside");
    await mkdir(outside);
    await writeFile(join(outside, "secret.txt"), "outside");
    await expect(call("read", {path: "../outside/secret.txt"})).rejects.toThrow("outside");
    await expect(call("write", {path: join(outside, "new.txt"), content: "no"})).rejects.toThrow("outside");
    const link = join(workspace, "external-link");
    await symlink(outside, link, process.platform === "win32" ? "junction" : "dir");
    await expect(call("read", {path: "external-link/secret.txt"})).rejects.toThrow("Symbolic links");
    await expect(call("write", {path: "external-link/new.txt", content: "no"})).rejects.toThrow("Symbolic links");
    expect(await readFile(join(outside, "secret.txt"), "utf8")).toBe("outside");
  });
  test("find and grep use installed ripgrep and respect ignored files", async () => {
    const directory = join(workspace, "search");
    await mkdir(directory);
    await writeFile(join(directory, ".gitignore"), "ignored.txt\n");
    await writeFile(join(directory, "match.txt"), "needle\n");
    await writeFile(join(directory, "ignored.txt"), "needle\n");
    const found = text(await call("find", {path: "search", pattern: "*.txt"}));
    expect(found).toContain("match.txt");
    expect(found).not.toContain("ignored.txt");
    // Native Pi grep follows ripgrep's default repo behavior. Create a local
    // repository marker so .gitignore participates on every test platform.
    await mkdir(join(directory, ".git"));
    const matches = text(await call("grep", {path: "search", pattern: "needle", literal: true}));
    expect(matches).toContain("match.txt:1: needle");
    expect(matches).not.toContain("ignored.txt");
  });
  test("requires a bounded shell deadline even for raw protocol callers", () => {
    expect(validateBuiltinRequest({protocol: 1, tool: "bash", cwd: workspace, args: {command: "echo ok"}}).args.timeout).toBe(30);
    for (const timeout of [0, Infinity, 601]) expect(() => validateBuiltinRequest({protocol: 1, tool: "bash", cwd: workspace, args: {command: "echo ok", timeout}})).toThrow("timeout");
    expect(() => validateBuiltinRequest({protocol: 1, tool: "unknown", cwd: workspace, args: {}})).toThrow("Invalid");
  });
  test("bash returns output and preserves nonzero exit as SDK isError", async () => {
    expect(text(await call("bash", {command: "printf 'pi-shell-ok\\n'"}))).toContain("pi-shell-ok");
    const failed = await call("bash", {command: "printf 'failure\\n'; exit 7"});
    expect(failed.isError).toBe(true);
    expect((failed.structuredContent as Record<string, unknown>).exit_code).toBe(7);
  });
  test("retains truncated shell output at a path the file nodes can read", async () => {
    const result = await call("bash", {command: "printf '%060000d' 0", timeout: 5});
    const outputPath = String(result.details?.fullOutputPath);
    expect(outputPath).toStartWith(".pi-output/");
    expect((await readFile(join(workspace, outputPath))).length).toBe(60000);
    expect(text(result)).toContain(outputPath);
    expect(text(await call("read", {path: outputPath}))).toContain("exceeds");
  });
  test("bash timeout stops its child and retains partial output", async () => {
    const started = performance.now();
    // Windows process-tree termination has variable overhead. A child whose
    // natural exit is well beyond the bound proves cancellation still works.
    await expect(call("bash", {command: "printf 'started\\n'; sleep 8", timeout: 1})).rejects.toThrow("timed out after 1");
    expect(performance.now() - started).toBeLessThan(7000);
  }, 12000);
  test("bash abort stops the owned execution", async () => {
    const controller = new AbortController();
    const started = performance.now();
    const result = call("bash", {command: "sleep 4", timeout: 10}, controller.signal);
    const timer = setTimeout(() => controller.abort(), 250);
    try {await expect(result).rejects.toThrow("aborted");}
    finally {clearTimeout(timer);}
    expect(performance.now() - started).toBeLessThan(3000);
  }, 7000);
  test("bash releases quiet inherited output pipes after the parent exits", async () => {
    const started = performance.now();
    try {
      expect(text(await call("bash", {command: "sleep 2 & printf 'parent-exited\\n'", timeout: 5}))).toContain("parent-exited");
      expect(performance.now() - started).toBeLessThan(1500);
    } finally {
      // The intentionally detached child expires itself; leave no live fixture.
      await new Promise(resolveDelay => setTimeout(resolveDelay, Math.max(0, 2200 - (performance.now() - started))));
    }
  }, 7000);
  test.skipIf(process.platform !== "win32")("PowerShell executes through its native SDK tool", async () => {
    expect(text(await call("powershell", {command: "Write-Output 'pi-powershell-ok'", timeout: 5}))).toContain("pi-powershell-ok");
  }, 10000);
});
