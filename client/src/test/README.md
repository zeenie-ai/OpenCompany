# Frontend Test Suite

Locks in the user-facing invariants from [docs-internal/ARCHIVE/credentials_panel.md §5](../../../docs-internal/ARCHIVE/credentials_panel.md).

## Run

```bash
cd client
bun run test           # one-shot (vitest run)
bun run test:watch     # watch mode
bun run test:coverage  # with v8 coverage report
```

Dependencies come from `bun install` at the repo root (the client is a workspace member).

## Layout

Test files live beside their subjects, almost all under `__tests__/` directories, across adapters/, app/, assets/icons/, components/, contexts/, features/ (chat/, home/), hooks/, lib/, store/, stores/, types/ and utils/. Targeted subsets: `bun run test:credentials`, `bun run test:nodepanels`.

## Tooling

- **Vitest** + **jsdom** for fast component tests
- **@testing-library/react** + **@testing-library/user-event** for behaviour assertions
- **builders.ts** for test-data factories — keep test bodies focused on deltas, not boilerplate
- **setup.ts** stubs `matchMedia` / `ResizeObserver` / `IntersectionObserver` (jsdom doesn't ship them)
- **waapi.ts** records Web Animations (`installWaapiStub()`) for tests that assert on motion; jsdom has no `Element.animate`

## What this suite does NOT test

- The internals of the shadcn / Radix primitives
- `react-flow` rendering
- Backend handlers (covered by `server/tests/credentials/`)
- Full OAuth round-trip with real X / Google (no network)
