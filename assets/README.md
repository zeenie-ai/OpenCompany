# Project artwork

Canonical source artwork and publishing images live here, grouped by use. Keep the generation prompts, provenance, masters and export scripts with the corresponding artwork. Generation dates belong in the records rather than directory names.

| Asset family | Location | Contents |
| --- | --- | --- |
| README and social banners | [brand/banners](brand/banners/README.md) | Platform exports, masters, previews, exact prompts and rebuild scripts |
| Selected banner concepts | [brand/banners/concepts](brand/banners/concepts/IMAGE-PROMPTS.md) | Selected workplace concept, persona refinement and original prompts |
| Employee portraits | [employees/avatars](employees/avatars/README.md) | Six portrait masters, 512px and 256px exports, lineup, prompts and provenance |
| Employee workflow illustration | [illustrations/employee-workflow](illustrations/employee-workflow/README.md) | README illustration, original diagram, refinements and generation record |
| Selected logo concepts | [brand/logo-concepts](brand/logo-concepts/README.md) | Retained raster references for the brand's logo development |

The root README uses [the canonical banner export](brand/banners/exports/github-readme-1600x700.jpg) and [the final workflow illustration](illustrations/employee-workflow/how-ai-employees-work.png) directly. Their click targets remain direct image previews.

## Application assets

The six 256px runtime portraits live in [client/src/assets/employee-avatars](../client/src/assets/employee-avatars/README.md), where Vite imports and fingerprints them for the app. They are exact copies of the [256px source exports](employees/avatars/avatars-256/). When changing the approved portraits, update those six runtime copies together; preserve their filenames and the fixed assignment order in the application.

The established vector logo implementation remains in [client/src/components/brand](../client/src/components/brand/geometry.ts), with its publishing variants in [media/brand](../media/brand/README.md). Technical architecture diagrams remain with their documentation in [docs/diagrams](../docs/diagrams/README.md).

Root artwork masters and generation archives are excluded from Docker and npm runtime packages. Only the app's imported runtime assets are shipped through the frontend build.
