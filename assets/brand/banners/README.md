# OpenCompany platform banners

Seven JPEG banners adapted from the selected professional AI-cat workplace. Use the files in `exports/` for upload; the original persona master is preserved. No platform accounts or repository settings have been changed.

[Preview all banners](previews/platform-overview.jpg) · [Create the downloadable banner pack](package.ps1)

| Placement | Export | Guidance |
| --- | --- | --- |
| GitHub README | [1600 × 700](exports/github-readme-1600x700.jpg) | A design choice for this README, not a GitHub size requirement. |
| GitHub repository social preview | [1280 × 640](exports/github-social-preview-1280x640.jpg) | Separate from the README; upload in repository settings. GitHub recommends this size and a file under 1 MB. [GitHub Docs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview) |
| X profile header | [1500 × 500](exports/x-header-1500x500.jpg) | X warns that 60 px at both the top and bottom may be cropped. Check the profile-photo overlap. [X Help](https://help.x.com/en/managing-your-account/common-issues-when-uploading-profile-photo) |
| LinkedIn personal background | [1584 × 396](exports/linkedin-personal-1584x396.jpg) | JPG/PNG under 8 MB. Profile-photo overlap and display vary with the viewport. [LinkedIn Help](https://www.linkedin.com/help/linkedin/answer/a568217/) |
| LinkedIn company Page cover | [1512 × 256](exports/linkedin-company-1512x256.jpg) | Current recommended size; maximum 3 MB. Allow for trimming, especially near the lower-right corner. [LinkedIn Help](https://www.linkedin.com/help/linkedin/answer/a563309/image-specifications-for-your-linkedin-pages-and-career-pages?lang=en) |
| YouTube channel banner | [2560 × 1440](exports/youtube-channel-2560x1440.jpg) | Maximum 6 MB. Essential content uses a conservative centered 1540 × 420 area, derived from YouTube's 1235 × 338 safe area at the 2048 × 1152 minimum upload size. [YouTube Help](https://support.google.com/youtube/answer/10456525?hl=en) |
| General social-link preview | [1200 × 630](exports/social-link-preview-1200x630.jpg) | A practical general-purpose export, not a universal platform requirement. |

Dimensions were checked against the linked platform guidance on 2026-11-09. Avatar overlays and cropping can vary by device, so review each platform's upload preview before saving.

## Source and reproduction

- [Selected persona master](masters/selected-persona-polish.png): preserved source artwork with the six employee personas, grumpy boss, customer, token input, finished-work handoff and payment.
- [Selected banner concepts and original prompts](concepts/IMAGE-PROMPTS.md): the retained base image and persona polish that led to this banner kit.
- [Exact generation prompts](prompts/): the composition adaptations use the built-in `image_gen` tool.
- [Export script](export.ps1): produces exact-size files using proportional fitting and padding, without stretching the artwork.
- [Packaging script](package.ps1): recreates `opencompany-platform-banners.zip` from the tracked kit and verifies the seven exports. The generated archive is ignored by Git.
- [Generation plan](generation-plan.json): records the reference and adaptation prompts.
- [Actual generation record](generation-record.json): maps every exact prompt to its output and distinguishes intermediate drafts from final sources.
- [Export manifest](export-manifest.json): exact dimensions, file sizes, source crops and proportional placement for each final file.

GitHub and the general link card use the selected full workplace. X and the company cover use the shallow row of employees so the token/work/payment actions survive narrow crops. LinkedIn personal and YouTube use the courtyard adaptation. The large cream area on the YouTube upload is intentional: the complete story stays inside its mobile-safe center.

The `previews/*-crop-guide.jpg` files show conservative crop/avatar simulations for review only. Upload the clean files in `exports/`.

The export files are the ready-to-upload deliverables. Keep the masters and prompts for future revisions.

On Windows, run `./export.ps1` from this directory to regenerate the JPEGs and previews, then `./package.ps1` to recreate the download archive. The export script uses Windows System.Drawing; no image-generation API call is needed to rebuild these files.

From the repository root:

```powershell
powershell -NoProfile -File assets/brand/banners/export.ps1
powershell -NoProfile -File assets/brand/banners/package.ps1
```

Current file references are relative to this kit. Original tool output paths and `original_reference` values are retained as historical provenance; they are not required for export or packaging.
