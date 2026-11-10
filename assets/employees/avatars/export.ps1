[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$avatarRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$avatarRoles = @(
    [pscustomobject]@{ Slug = 'builder'; Title = 'Builder'; Persona = 'Focused software craftsperson' },
    [pscustomobject]@{ Slug = 'grower'; Title = 'Grower'; Persona = 'Confident creative strategist' },
    [pscustomobject]@{ Slug = 'chief-of-staff'; Title = 'Chief of Staff'; Persona = 'Calm, organized coordinator' },
    [pscustomobject]@{ Slug = 'front-desk'; Title = 'Front Desk'; Persona = 'Warm, attentive client service' },
    [pscustomobject]@{ Slug = 'researcher'; Title = 'Researcher'; Persona = 'Thoughtful, inquisitive analyst' },
    [pscustomobject]@{ Slug = 'treasurer'; Title = 'Treasurer'; Persona = 'Precise payment specialist' }
)

# Validate every source before creating exports; non-square inputs need a deliberate
# composition decision instead of an implicit crop or stretch.
$avatarSourceRecords = @{}
foreach ($avatarRole in $avatarRoles) {
    $avatarSourcePath = Join-Path $avatarRoot ('masters/{0}.png' -f $avatarRole.Slug)
    if (-not (Test-Path -LiteralPath $avatarSourcePath -PathType Leaf)) {
        throw "Missing portrait master: $avatarSourcePath"
    }
    $avatarSource = [System.Drawing.Image]::FromFile($avatarSourcePath)
    try {
        if ($avatarSource.Width -ne $avatarSource.Height) {
            throw "Portrait '$($avatarRole.Slug)' must be square; found $($avatarSource.Width) x $($avatarSource.Height)."
        }
        $avatarSourceRecords[$avatarRole.Slug] = [ordered]@{
            file = 'masters/{0}.png' -f $avatarRole.Slug
            width = $avatarSource.Width
            height = $avatarSource.Height
            bytes = (Get-Item -LiteralPath $avatarSourcePath).Length
            sha256 = (Get-FileHash -LiteralPath $avatarSourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    finally {
        $avatarSource.Dispose()
    }
}

foreach ($avatarDirectory in @('avatars-512', 'avatars-256', 'previews')) {
    [System.IO.Directory]::CreateDirectory((Join-Path $avatarRoot $avatarDirectory)) | Out-Null
}

function Set-AvatarGraphicsQuality {
    param([System.Drawing.Graphics]$Graphics)
    $Graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $Graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $Graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $Graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $Graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
}

function Save-SquareAvatar {
    param(
        [System.Drawing.Image]$Source,
        [int]$Size,
        [string]$Destination
    )
    $avatarBitmap = [System.Drawing.Bitmap]::new($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $avatarGraphics = [System.Drawing.Graphics]::FromImage($avatarBitmap)
    $avatarImageAttributes = [System.Drawing.Imaging.ImageAttributes]::new()
    try {
        Set-AvatarGraphicsQuality -Graphics $avatarGraphics
        $avatarGraphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
        $avatarGraphics.Clear([System.Drawing.Color]::Transparent)
        $avatarImageAttributes.SetWrapMode([System.Drawing.Drawing2D.WrapMode]::TileFlipXY)
        $avatarRectangle = [System.Drawing.Rectangle]::new(0, 0, $Size, $Size)
        $avatarGraphics.DrawImage($Source, $avatarRectangle, 0, 0, $Source.Width, $Source.Height,
            [System.Drawing.GraphicsUnit]::Pixel, $avatarImageAttributes)
        $avatarBitmap.Save($Destination, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $avatarImageAttributes.Dispose()
        $avatarGraphics.Dispose()
        $avatarBitmap.Dispose()
    }
}

$avatarManifestEntries = @()
foreach ($avatarRole in $avatarRoles) {
    $avatarSourcePath = Join-Path $avatarRoot ('masters/{0}.png' -f $avatarRole.Slug)
    $avatarSource = [System.Drawing.Image]::FromFile($avatarSourcePath)
    $avatarExportRecords = @()
    try {
        foreach ($avatarSize in @(512, 256)) {
            $avatarRelativePath = 'avatars-{0}/{1}.png' -f $avatarSize, $avatarRole.Slug
            $avatarDestination = Join-Path $avatarRoot $avatarRelativePath
            Save-SquareAvatar -Source $avatarSource -Size $avatarSize -Destination $avatarDestination
            $avatarExportRecords += [ordered]@{
                file = $avatarRelativePath
                width = $avatarSize
                height = $avatarSize
                bytes = (Get-Item -LiteralPath $avatarDestination).Length
                sha256 = (Get-FileHash -LiteralPath $avatarDestination -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
    }
    finally {
        $avatarSource.Dispose()
    }
    $avatarManifestEntries += [ordered]@{
        slug = $avatarRole.Slug
        role = $avatarRole.Title
        persona = $avatarRole.Persona
        source = $avatarSourceRecords[$avatarRole.Slug]
        exports = $avatarExportRecords
    }
}

# Circular clips are previews only; the deliverable PNGs retain the complete square.
$avatarSheet = [System.Drawing.Bitmap]::new(1200, 900, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
$avatarSheetGraphics = [System.Drawing.Graphics]::FromImage($avatarSheet)
$avatarInk = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(47, 39, 34))
$avatarMutedInk = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(103, 83, 68))
$avatarAccent = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(216, 170, 126), 3)
$avatarTitleFont = [System.Drawing.Font]::new('Georgia', 29, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$avatarRoleFont = [System.Drawing.Font]::new('Georgia', 25, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$avatarPersonaFont = [System.Drawing.Font]::new('Segoe UI', 17, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$avatarNoteFont = [System.Drawing.Font]::new('Segoe UI', 15, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$avatarCenteredText = [System.Drawing.StringFormat]::new()
$avatarCenteredText.Alignment = [System.Drawing.StringAlignment]::Center
$avatarCenteredText.LineAlignment = [System.Drawing.StringAlignment]::Center
$avatarPreviewRelativePath = 'previews/employee-lineup.jpg'
$avatarPreviewPath = Join-Path $avatarRoot $avatarPreviewRelativePath
try {
    Set-AvatarGraphicsQuality -Graphics $avatarSheetGraphics
    $avatarSheetGraphics.Clear([System.Drawing.Color]::FromArgb(250, 239, 226))
    $avatarSheetGraphics.DrawString('OpenCompany AI employees', $avatarTitleFont, $avatarInk,
        [System.Drawing.RectangleF]::new(30, 26, 1140, 44), $avatarCenteredText)

    for ($avatarIndex = 0; $avatarIndex -lt $avatarRoles.Count; $avatarIndex++) {
        $avatarRole = $avatarRoles[$avatarIndex]
        $avatarColumn = $avatarIndex % 3
        $avatarRow = [int][System.Math]::Floor($avatarIndex / 3)
        $avatarCenterX = 216 + (384 * $avatarColumn)
        $avatarTop = 106 + (360 * $avatarRow)
        $avatarCircle = [System.Drawing.Rectangle]::new($avatarCenterX - 128, $avatarTop, 256, 256)
        $avatarCirclePath = [System.Drawing.Drawing2D.GraphicsPath]::new()
        $avatarPortrait = [System.Drawing.Image]::FromFile((Join-Path $avatarRoot ('avatars-512/{0}.png' -f $avatarRole.Slug)))
        try {
            $avatarCirclePath.AddEllipse($avatarCircle)
            $avatarSavedState = $avatarSheetGraphics.Save()
            try {
                $avatarSheetGraphics.SetClip($avatarCirclePath, [System.Drawing.Drawing2D.CombineMode]::Intersect)
                $avatarSheetGraphics.DrawImage($avatarPortrait, $avatarCircle)
            }
            finally {
                $avatarSheetGraphics.Restore($avatarSavedState)
            }
            $avatarSheetGraphics.DrawEllipse($avatarAccent, $avatarCircle)
        }
        finally {
            $avatarPortrait.Dispose()
            $avatarCirclePath.Dispose()
        }
        $avatarSheetGraphics.DrawString($avatarRole.Title, $avatarRoleFont, $avatarInk,
            [System.Drawing.RectangleF]::new($avatarCenterX - 185, $avatarTop + 267, 370, 36), $avatarCenteredText)
        $avatarSheetGraphics.DrawString($avatarRole.Persona, $avatarPersonaFont, $avatarMutedInk,
            [System.Drawing.RectangleF]::new($avatarCenterX - 185, $avatarTop + 305, 370, 30), $avatarCenteredText)
    }

    $avatarSheetGraphics.DrawString('Circular crop preview - complete square portraits supplied at 512 px and 256 px',
        $avatarNoteFont, $avatarMutedInk, [System.Drawing.RectangleF]::new(30, 844, 1140, 28), $avatarCenteredText)
    $avatarJpegCodec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
        Where-Object { $_.MimeType -eq 'image/jpeg' } | Select-Object -First 1
    $avatarJpegParameters = [System.Drawing.Imaging.EncoderParameters]::new(1)
    try {
        $avatarJpegParameters.Param[0] = [System.Drawing.Imaging.EncoderParameter]::new(
            [System.Drawing.Imaging.Encoder]::Quality, [long]92)
        $avatarSheet.Save($avatarPreviewPath, $avatarJpegCodec, $avatarJpegParameters)
    }
    finally {
        $avatarJpegParameters.Dispose()
    }
}
finally {
    $avatarCenteredText.Dispose()
    $avatarNoteFont.Dispose()
    $avatarPersonaFont.Dispose()
    $avatarRoleFont.Dispose()
    $avatarTitleFont.Dispose()
    $avatarAccent.Dispose()
    $avatarMutedInk.Dispose()
    $avatarInk.Dispose()
    $avatarSheetGraphics.Dispose()
    $avatarSheet.Dispose()
}

$avatarManifest = [ordered]@{
    schema_version = 1
    generated_by = 'export.ps1'
    resize_method = 'HighQualityBicubic; square sources required; no crop or stretching'
    roles = $avatarManifestEntries
    preview = [ordered]@{
        file = $avatarPreviewRelativePath
        width = 1200
        height = 900
        bytes = (Get-Item -LiteralPath $avatarPreviewPath).Length
        sha256 = (Get-FileHash -LiteralPath $avatarPreviewPath -Algorithm SHA256).Hash.ToLowerInvariant()
        note = 'Circular clips for QA; exported avatar PNGs remain square.'
    }
}
[System.IO.File]::WriteAllText((Join-Path $avatarRoot 'manifest.json'),
    ($avatarManifest | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
Write-Output 'Exported 12 square PNG avatars, one circular-preview lineup, and manifest.json.'
