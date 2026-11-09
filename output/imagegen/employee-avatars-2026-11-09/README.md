# OpenCompany employee profile pictures

Six professional AI cat employees, generated with the built-in image_gen tool from the approved workplace banner on 2026-11-09.

[View the team lineup](previews/employee-lineup.jpg)

| Employee | Persona | 512 px | 256 px | Actual generation prompt |
| --- | --- | --- | --- | --- |
| Builder | Focused software craftsperson | [PNG](avatars-512/builder.png) | [PNG](avatars-256/builder.png) | [Prompt](prompts/builder.txt) |
| Grower | Confident creative strategist | [PNG](avatars-512/grower.png) | [PNG](avatars-256/grower.png) | [Prompt](prompts/grower.txt) |
| Chief of Staff | Calm operations coordinator | [PNG](avatars-512/chief-of-staff.png) | [PNG](avatars-256/chief-of-staff.png) | [Prompt](prompts/chief-of-staff.txt) |
| Front Desk | Warm client-service professional | [PNG](avatars-512/front-desk.png) | [PNG](avatars-256/front-desk.png) | [Prompt](prompts/front-desk.txt) |
| Researcher | Curious evidence-led analyst | [PNG](avatars-512/researcher.png) | [PNG](avatars-256/researcher.png) | [Prompt](prompts/researcher.txt) |
| Treasurer | Precise payment specialist | [PNG](avatars-512/treasurer.png) | [PNG](avatars-256/treasurer.png) | [Prompt](prompts/treasurer.txt) |

## Files and use

- `masters/`: six original square 1254 x 1254 PNG generations, retained without retouching.
- `avatars-512/`: square PNGs for profile pages and larger employee cards.
- `avatars-256/`: smaller square PNGs for employee lists, chat and experimentation.
- `previews/employee-lineup.jpg`: 1200 x 900 contact sheet showing circular crops and role labels.
- `prompts/`: the exact six prompts passed to image_gen.
- `generation-record.json`: reference order, generation provenance and original tool output paths.
- `manifest.json`: actual dimensions, byte counts and SHA-256 hashes for every master, export and preview.

Use a centered circular mask with `object-fit: cover` and `object-position: center`, or retain the complete square. The circular preview was checked for ear, eyewear and headset clearance. Keep the employee's name and role as accessible application text outside the image. Portraits contain no embedded labels.

This is a reusable asset pack for future employee profiles. Application assignment is not part of this generation.

## Art direction and provenance

The [approved persona-polished workplace banner](../platform-banners-2026-11-09/masters/selected-persona-polish.png) supplied character identities, clothing and the warm crafted 3D style. Builder was generated first; its portrait was the second reference for the remaining five portraits to maintain camera height, face scale, background and lighting. The banner remained the first reference for all six.

Distinct identities: ginger Builder in slate blue; white Grower with a charcoal ear patch and sage blazer; tuxedo Chief of Staff in navy; cream colorpoint Front Desk with blue eyes, burgundy blazer and headset; calico Researcher with round glasses and oatmeal knitwear; silver tabby Treasurer with rectangular glasses and a forest-green waistcoat.

The tool was asked for approximately 1024 x 1024 draft portraits and returned 1254 x 1254 PNGs. Exporting only resizes the complete square; it does not regenerate, crop or retouch the images. The circular masks and captions are used only in the preview.

## Rebuild exports

From the repository root on Windows:

```powershell
powershell -NoProfile -File output/imagegen/employee-avatars-2026-11-09/export.ps1
```

The script validates all six square sources, recreates 512 px and 256 px PNGs with high-quality bicubic resampling, and rebuilds the lineup and manifest. It does not call image generation.
