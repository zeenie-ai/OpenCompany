# OpenCompany brand assets

## Logo: the mark on its own

These files are the Open Council mark without the OpenCompany name. They are the same paths the app
draws, from [client/src/components/brand/geometry.ts](../../client/src/components/brand/geometry.ts).
The logo is one colour: black on light backgrounds, white on dark ones.

| File | What it is |
|---|---|
| [opencompany-mark.svg](./opencompany-mark.svg) | Black, transparent background, cropped to the mark (720 by 677) |
| [opencompany-mark-white.svg](./opencompany-mark-white.svg) | White, transparent background, cropped to the mark |
| [opencompany-mark.png](./opencompany-mark.png) | Black on transparent, 1024 by 1024 |
| [opencompany-mark-white.png](./opencompany-mark-white.png) | White on transparent, 1024 by 1024 |
| [opencompany-mark-on-white.png](./opencompany-mark-on-white.png) | Black on white, 1024 by 1024 |
| [opencompany-mark-on-black.png](./opencompany-mark-on-black.png) | White on black, 1024 by 1024 |

Use an SVG wherever it is accepted. The PNGs are squares for avatars and app listings: the mark is
720 px wide and centred on its box, so a circular crop does not touch it.

## Changing the mark

`client/src/components/brand/__tests__/appIcons.test.ts` checks that both SVGs carry `MARK_PATH`
and `MARK_VIEWBOX` from `geometry.ts`, so a new mark fails that test until its path is pasted into
them. Then render the PNGs again: put the path in an SVG with `viewBox="-512 -579.5 1024 1024"`,
`width` and `height` 1024 (that canvas is centred on the mark's box), add a `rect` filling the
canvas for the black and white backgrounds, and render it with sharp at density 384, resized to
1024, as `desktop/scripts/gen-icons.ts` renders the desktop icon.

The concept the mark was redrawn from, with the name below it, is
[output/imagegen/opencompany-selected/open-council-three.png](../../output/imagegen/opencompany-selected/open-council-three.png).

## README banner

[opencompany-readme-banner.jpg](./opencompany-readme-banner.jpg) is the 1600 × 700 banner used by the root README. It shows the professional AI cat employees turning tokens into finished work, with a customer receiving the work and paying the team.

This is a byte-for-byte copy of the approved [GitHub README export](../../output/imagegen/platform-banners-2026-11-09/exports/github-readme-1600x700.jpg). Keep this stable publishing path in sync with that export when updating the artwork. The [platform banner kit](../../output/imagegen/platform-banners-2026-11-09/README.md) contains the exact prompts, masters, additional sizes, and export instructions.
