import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'
import { readFileSync } from 'fs'
import { visualizer } from 'rollup-plugin-visualizer'

// Read root package.json for app version (one level up from client/)
// Falls back to '0.0.0' in Docker where only client/ is in the build context
let appVersion = '0.0.0'
try {
  const rootPkg = JSON.parse(readFileSync(resolve(process.cwd(), '..', 'package.json'), 'utf-8'))
  appVersion = rootPkg.version
} catch { /* Docker build - root package.json not available */ }

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Load env from parent directory (root .env) for local development
  const fileEnv = loadEnv(mode, resolve(process.cwd(), '..'), '')

  // In Docker, env vars are set via ENV in Dockerfile, accessible via process.env
  // Priority: process.env (Docker) > fileEnv (local .env) > defaults
  const getEnv = (key, defaultValue = '') => {
    return process.env[key] || fileEnv[key] || defaultValue
  }

  // APP_VERSION: from root package.json locally, or VITE_APP_VERSION build arg in Docker
  const version = getEnv('VITE_APP_VERSION', '') || appVersion

  // Bundle visualizer: set ANALYZE=1 to emit dist/stats.html alongside the
  // normal build output. Zero cost when ANALYZE is unset — the plugin is
  // simply not added to the plugin chain. See docs-internal/credentials_scaling
  // for bundle-budget targets (main < 200 KB gz, per-panel < 50 KB gz).
  const analyze = !!getEnv('ANALYZE', '')

  // React Compiler 1.0 — auto-memoization. Scoped to the credentials
  // module first (Phase 7.5 of the credentials-scaling plan) so any perf
  // regressions are isolated from the rest of the app. Expand the
  // `sources` predicate module-by-module as each area is verified.
  //
  // Ref: docs-internal/credentials_scaling/research_react_stack.md
  //      docs-internal/platform_refactor/PLATFORM_REFACTOR_RFC.md Phase 7.5
  // Phase 7: broadened from credentials-only to the whole src/ now that
  // antd is retired. Exclude node_modules and the generated shadcn ui/
  // files (they don't benefit from the compiler and it can confuse CVA).
  const reactCompilerConfig = {
    target: '19',
    sources: (filename) => {
      if (typeof filename !== 'string') return false
      const normalized = filename.replace(/\\/g, '/')
      if (!normalized.includes('/src/')) return false
      if (normalized.includes('/src/components/ui/')) return false
      return true
    },
  }

  return {
    plugins: [
      tailwindcss(),
      react({
        babel: {
          plugins: [
            ['babel-plugin-react-compiler', reactCompilerConfig],
          ],
        },
      }),
      ...(analyze
        ? [
            visualizer({
              filename: 'dist/stats.html',
              gzipSize: true,
              brotliSize: true,
              template: 'treemap',
              sourcemap: true,
            }),
          ]
        : []),
    ],
    // Expose VITE_ prefixed env vars to client code via import.meta.env
    define: {
      __APP_VERSION__: JSON.stringify(version),
      'import.meta.env.VITE_PYTHON_SERVICE_URL': JSON.stringify(getEnv('VITE_PYTHON_SERVICE_URL', '')),
      'import.meta.env.VITE_WHATSAPP_SERVICE_URL': JSON.stringify(getEnv('VITE_WHATSAPP_SERVICE_URL', '')),
      'import.meta.env.VITE_ANDROID_RELAY_URL': JSON.stringify(getEnv('VITE_ANDROID_RELAY_URL', '')),
    },
    resolve: {
      alias: {
        '@': resolve(process.cwd(), 'src'),
      },
    },
    server: {
      port: parseInt(getEnv('VITE_CLIENT_PORT', '3000')),
      strictPort: false,
      host: true
    },
    optimizeDeps: {
      // `company dev --force` sets VITE_FORCE to re-run dependency
      // pre-bundling — Vite's own recovery for "Outdated Optimize Dep"
      // (equivalent to `vite --force`, which can't be threaded through
      // the pnpm run -> npm run indirection as an argv flag).
      force: !!getEnv('VITE_FORCE', ''),
      // Pre-bundle the heavy deps reached through lazily-loaded panels
      // (chat/markdown stack, canvas) so a late discovery can't trigger
      // a mid-session re-optimization — the root cause of the
      // "Outdated Optimize Dep" 504 (vitejs/vite#14284).
      include: [
        'reactflow',
        'react-markdown',
        'remark-gfm',
        'remark-breaks',
        'prismjs',
        'react-simple-code-editor',
        '@uiw/react-json-view',
      ],
    },
    build: {
      // ES2022 unlocks native `findLast`, optional-chaining-assignment,
      // class-fields without polyfills. Browser baseline: Chrome 94+,
      // Firefox 93+, Safari 15.4+ — within React 19 / Tailwind 4's range.
      target: 'es2022',
      // Lowered from 1500 KB once manualChunks splits out the heavy libs.
      // 850 KB covers the largest legitimate chunk today (`vendor-icons`,
      // ~800 KB raw / 210 KB gz from lucide-react + @lobehub/icons brand
      // SVGs that resist tree-shaking). The cap still catches new chunk
      // bloat in the entry / route bundles.
      chunkSizeWarningLimit: 850,
      // Emit sourcemaps only when running the bundle analyzer so the
      // visualizer can attribute bytes to source files accurately.
      // Skipped in normal production builds to keep build time down.
      sourcemap: analyze,
      rollupOptions: {
        output: {
          // Split heavy npm libs into their own cacheable chunks so the
          // main bundle stays lean and unrelated dependency churn doesn't
          // bust the user's cache. Group rationale:
          //   vendor-react    — core framework + form runtime
          //   vendor-flow     — reactflow is huge and only canvas pages use it
          //   vendor-radix    — accessibility primitives, used widely
          //   vendor-icons    — lucide + lobehub brand icons
          //   vendor-query    — TanStack Query + persistence
          //   vendor-markdown — markdown rendering stack (chat / docs panels)
          //   vendor-misc     — small but heavyweight utilities
          // Anything not listed falls into the default route/entry chunks.
          manualChunks: {
            'vendor-react': [
              'react',
              'react-dom',
              'react-hook-form',
              '@hookform/resolvers',
            ],
            'vendor-flow': ['reactflow'],
            'vendor-radix': [
              'radix-ui',
              '@radix-ui/react-collapsible',
              '@radix-ui/react-dialog',
              '@radix-ui/react-slot',
            ],
            'vendor-icons': ['lucide-react', '@lobehub/icons'],
            'vendor-query': [
              '@tanstack/react-query',
              '@tanstack/query-sync-storage-persister',
              '@tanstack/react-query-persist-client',
              '@lukemorales/query-key-factory',
            ],
            'vendor-markdown': [
              'react-markdown',
              'remark-gfm',
              'remark-breaks',
              'prismjs',
              'react-simple-code-editor',
              '@uiw/react-json-view',
            ],
            'vendor-misc': [
              'idb-keyval',
              'fuzzysort',
              'cmdk',
              'sonner',
              'qrcode.react',
            ],
          },
        },
      },
    },
  }
})
