# OpenCompany — selected logos

User-selected set: Broad Shield (Broadguard), Open Council Three, and the original Heraldic Flight.

| Selected name | File | Source concept |
|---|---|---|
| Broad Shield | [broad-shield.png](./broad-shield.png) | Broadguard |
| Open Council Three | [open-council-three.png](./open-council-three.png) | Open Council Three |
| Heraldic Flight | [heraldic-flight.png](./heraldic-flight.png) | Original Heraldic Flight |

Open [index.html](./index.html) to compare the selected logos. PNG files are exact copies of the selected generated concepts.

The source explorations are archived locally in `tmp/logo-explorations/`, which is gitignored.

## Adopted

Open Council Three became the logo on 2026-09-30. The app redraws it as vector paths in
[client/src/components/brand/geometry.ts](../../../client/src/components/brand/geometry.ts):
the mark measured from [open-council-three.png](./open-council-three.png) and made exactly
three-fold symmetric, and the wordmark set in Newsreader (SIL Open Font License 1.1) and
converted to outlines. Both are one colour, the text colour: black on light themes, white on
dark. The favicon and the desktop icon embed the same mark path; the Home orb is its 3D
version. This PNG stays here as the reference. The mark on its own, without the name, is in
[media/brand](../../../media/brand/) as SVG and PNG.
