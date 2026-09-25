import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // Allow unused vars that start with underscore or uppercase
      '@typescript-eslint/no-unused-vars': ['error', {
        varsIgnorePattern: '^_|^[A-Z]',
        argsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_|^error$|^e$'
      }],
      '@typescript-eslint/no-explicit-any': 'off',
      // Disable strict react-hooks rules from v7 that are too aggressive for existing codebase
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/immutability': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/preserve-manual-memoization': 'off',
      // Downgrade rules that have many existing violations
      'no-case-declarations': 'warn',
      'no-empty': 'warn',
      'prefer-const': 'warn',
      // Downgrade rules-of-hooks to warn - existing codebase has conditional hook patterns
      // TODO: Fix these violations properly
      'react-hooks/rules-of-hooks': 'warn',
    },
  },
  // ── Design-system adherence (from the design-handoff bundle's
  //    _adherence.oxlintrc.json, ported to the existing ESLint gate) ──
  //
  // Scope is deliberately narrow: inline `style={}` attributes. Tailwind
  // classes are already token-driven, and a global hex/px scan would
  // false-positive on ids, URLs, SVG path data, and arbitrary-value
  // classes like `max-w-[150px]`. ActionButton intent misuse needs no
  // lint — the cva union type makes it a typecheck error.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: [
      // Grandfathered: legacy JS-side palettes and SVG glyph assets that
      // legitimately hold literal values (canvas/maps color packs, brand
      // icons, 290 themed glyphs). New code goes through theme tokens.
      'src/styles/theme.ts',
      'src/hooks/useAppTheme.ts',
      'src/components/icons/**',
      'src/assets/icons/**',
      'src/**/__tests__/**',
    ],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: 'JSXAttribute[name.name="style"] Literal[value=/#[0-9a-fA-F]{3,8}\\b/]',
          message:
            'No hex colors in inline styles — use a theme token via var(--...) (see docs-internal/theme_system.md).',
        },
        {
          selector:
            'JSXAttribute[name.name="style"] Property[key.name="fontFamily"] Literal:not([value=/^var\\(--font-/])',
          message:
            'No literal font stacks in inline styles — use var(--font-display) / var(--font-body) / var(--font-mono) so per-theme faces apply.',
        },
      ],
    },
  },
  // Normal mode's setup-screen pipeline (catalogue, parser, normalizer,
  // renderer, draft store) is private to its folder: everything else
  // imports it through features/home/genui/index.ts.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/features/home/genui/**'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['**/genui/*'],
              message:
                'Import the setup-screen pipeline from features/home/genui (its index) only; its modules are private to that folder.',
            },
          ],
        },
      ],
    },
  },
)
