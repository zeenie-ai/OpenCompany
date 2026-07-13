#!/usr/bin/env node

// Deprecated compatibility entry point. Keeping this wrapper separate from
// cli.js makes alias detection reliable through npm's Windows .cmd/.ps1 shims,
// which invoke the target JavaScript file rather than preserving the bin name.
process.env.OPENCOMPANY_LEGACY_ALIAS = 'machina';
await import('./cli.js');
