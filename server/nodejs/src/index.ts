/**
 * JS Code Execution Server (runs on bun)
 * Thin HTTP layer - all parameters from environment or requests
 */

import express, { Request, Response, NextFunction } from 'express';
import rateLimit from 'express-rate-limit';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// All configuration from environment variables
const PORT = parseInt(process.env.NODEJS_EXECUTOR_PORT ?? '', 10);
if (!PORT) {
  // Spawned by nodes/code/_runtime.py, which always injects the port
  // (canonical default lives in .env.template). No hardcoded fallback.
  throw new Error('NODEJS_EXECUTOR_PORT is not set - defaults live in .env.template');
}
const HOST = process.env.NODEJS_EXECUTOR_HOST ?? 'localhost';
const BODY_LIMIT = process.env.NODEJS_EXECUTOR_BODY_LIMIT ?? '10mb';
const USER_PACKAGES_DIR = process.env.NODEJS_USER_PACKAGES_DIR ?? path.join(__dirname, '..', 'user-packages');

// The runtime this process runs on is also the package manager for the
// user-packages tree: `process.execPath` is bun, and `bun add` installs
// from the npm registry with no node or npm on the machine.
const BUN = process.execPath;
const bunRuntime = (globalThis as {
  Bun?: {
    version: string;
    Transpiler: new (options: { loader: 'ts'; target: 'node' }) => {
      transformSync(code: string): string;
    };
  };
}).Bun;
const bunVersion = bunRuntime?.version ?? null;

const app = express();
app.use(express.json({ limit: BODY_LIMIT }));

// The package routes touch the filesystem and spawn `bun add`. The only
// caller is the same-machine Python backend, so the ceiling is generous;
// it exists so a stray loop cannot hammer the disk or the registry.
const packageRouteLimiter = rateLimit({
  windowMs: 60_000,
  limit: 120,
  standardHeaders: true,
  legacyHeaders: false,
});

interface ExecuteRequest {
  code: string;
  language?: 'javascript' | 'typescript';
  input_data?: Record<string, unknown>;
  timeout?: number;
}

// Health check
app.get('/health', (_req: Request, res: Response) => {
  res.json({
    status: 'healthy',
    service: 'nodejs-executor',
    runtime: bunVersion ? 'bun' : 'node',
    runtime_version: bunVersion ?? process.version,
    // Kept for the Python client's health dict; under bun this is the
    // Node API level bun reports, not a Node install.
    node_version: process.version,
  });
});

// TypeScript is type-stripped by bun's own transpiler before it reaches the
// vm context (which evaluates JavaScript only). Type annotations, interfaces
// and enums therefore work in typescriptExecutor; a syntax error surfaces as
// the same {success: false, error} envelope as a runtime error.
const tsTranspiler = bunRuntime ? new bunRuntime.Transpiler({ loader: 'ts', target: 'node' }) : null;

function prepareSource(code: string, language: ExecuteRequest['language']): string {
  if (language !== 'typescript') return code;
  if (!tsTranspiler) throw new Error('TypeScript execution needs the bun runtime');
  return tsTranspiler.transformSync(code);
}

// Execute code - all parameters from request body
app.post('/execute', (req: Request, res: Response) => {
  const { code, language = 'javascript', input_data = {}, timeout = 30000 } = req.body as ExecuteRequest;

  if (!code || typeof code !== 'string') {
    res.status(400).json({ success: false, error: 'Missing or invalid "code" field' });
    return;
  }

  const startTime = Date.now();
  const consoleOutput: string[] = [];

  const capturedConsole = {
    log: (...args: unknown[]) => consoleOutput.push(args.map(String).join(' ')),
    error: (...args: unknown[]) => consoleOutput.push(`[ERROR] ${args.map(String).join(' ')}`),
    warn: (...args: unknown[]) => consoleOutput.push(`[WARN] ${args.map(String).join(' ')}`),
    info: (...args: unknown[]) => consoleOutput.push(args.map(String).join(' ')),
  };

  const sandbox = {
    console: capturedConsole,
    input_data,
    output: undefined as unknown,
    JSON, Math, Date, Array, Object, String, Number, Boolean, RegExp, Map, Set, Promise,
    setTimeout, setInterval, clearTimeout, clearInterval,
  };

  try {
    const context = vm.createContext(sandbox);
    // This service IS the sandboxed JS executor for the pythonExecutor /
    // javascriptExecutor workflow nodes. `vm` is not a security boundary
    // (per https://nodejs.org/api/vm.html#vmcreatecontextcontextobject-options);
    // the deployment context is: server binds to localhost only (line 16,
    // default 'localhost') and is invoked exclusively by the same-machine
    // Python backend via NodeJSClient. Public network exposure is the
    // operator's responsibility. CodeQL's js/code-injection finding on this
    // line is by design and is dismissed on the repository as "won't fix".
    vm.runInContext(prepareSource(code, language), context, { timeout, filename: language === 'typescript' ? 'user-code.ts' : 'user-code.js' });

    res.json({
      success: true,
      output: sandbox.output,
      console_output: consoleOutput.join('\n'),
      execution_time_ms: Date.now() - startTime,
    });
  } catch (error) {
    res.json({
      success: false,
      error: error instanceof Error ? error.message : String(error),
      console_output: consoleOutput.join('\n'),
      execution_time_ms: Date.now() - startTime,
    });
  }
});

function ensureUserPackagesTree(): void {
  if (!existsSync(USER_PACKAGES_DIR)) mkdirSync(USER_PACKAGES_DIR, { recursive: true });
  const manifest = path.join(USER_PACKAGES_DIR, 'package.json');
  if (!existsSync(manifest)) {
    // bun add would write one, but a private manifest keeps the tree from
    // ever looking publishable and pins its name.
    writeFileSync(manifest, JSON.stringify({ name: 'opencompany-user-packages', private: true }, null, 2) + '\n');
  }
}

// Install packages - package list from request.
// Localhost-only service (see server.listen at the bottom); same trust
// boundary as the /execute sandbox.
app.post('/packages/install', packageRouteLimiter, (req: Request, res: Response) => {
  const { packages } = req.body as { packages: string[] };

  if (!packages || !Array.isArray(packages) || packages.length === 0) {
    res.status(400).json({ success: false, error: 'Missing or invalid "packages" array' });
    return;
  }

  const validPattern = /^(@[\w-]+\/)?[\w-]+(@[\w.-]+)?$/;
  const invalid = packages.filter(p => !validPattern.test(p));
  if (invalid.length > 0) {
    res.status(400).json({ success: false, error: `Invalid package names: ${invalid.join(', ')}` });
    return;
  }

  try {
    ensureUserPackagesTree();
    // execFileSync (argv array, no shell) instead of execSync with template
    // string. The regex above already validates names, but going through
    // execFileSync removes the shell from the path entirely as
    // defense-in-depth.
    execFileSync(BUN, ['add', '--no-progress', ...packages], { cwd: USER_PACKAGES_DIR, timeout: 60000 });
    res.json({ success: true, message: `Installed: ${packages.join(', ')}` });
  } catch (error) {
    res.status(500).json({ success: false, error: error instanceof Error ? error.message : String(error) });
  }
});

// List packages — same localhost-only trust boundary as above. The tree's
// own manifest is the source of truth (what `bun add` wrote), so no
// package-manager listing command is needed.
app.get('/packages', packageRouteLimiter, (_req: Request, res: Response) => {
  try {
    const manifest = JSON.parse(readFileSync(path.join(USER_PACKAGES_DIR, 'package.json'), 'utf-8')) as {
      dependencies?: Record<string, string>;
    };
    const installed: Record<string, { version: string }> = {};
    for (const [name, version] of Object.entries(manifest.dependencies ?? {})) {
      if (existsSync(path.join(USER_PACKAGES_DIR, 'node_modules', name))) installed[name] = { version };
    }
    res.json({ success: true, packages: installed });
  } catch {
    res.json({ success: true, packages: {} });
  }
});

app.use((err: Error, _req: Request, res: Response, _next: NextFunction) => {
  console.error('Server error:', err);
  res.status(500).json({ success: false, error: 'Internal server error' });
});

app.listen(PORT, HOST, () => {
  console.log(`JS Executor running on http://${HOST}:${PORT}`);
});
