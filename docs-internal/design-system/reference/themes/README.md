# Theme CSS — live source is authoritative

The 13 theme snapshots that used to live here were removed: they predated
the hex + `color-mix()` migration (they still carried the retired HSL
shadcn bridge, and lacked the `--code-*` / `--tint-*` tiers), and a second
vendored copy is exactly how they went stale.

**Read the live files instead:** [`client/src/themes/`](../../../../client/src/themes/)
— 4 files (`base.css`, `animations.css`, `light.css` and `dark.css`).
They ARE the design system's theme layer; the external handoff
bundle shipped a byte-identical copy of them and named them its source of
truth.

Prose contracts for the layer live in
[`../../guidelines/THEMES.md`](../../guidelines/THEMES.md) and the merged
handoff brief at [`../../HANDOFF.md`](../../HANDOFF.md); the panel-by-panel
visual target is [`../../reference-mockup/`](../../reference-mockup/).
