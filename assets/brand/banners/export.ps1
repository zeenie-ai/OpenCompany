param([string]$OutputRoot = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$exportRoot = Join-Path $OutputRoot 'exports'
$previewRoot = Join-Path $OutputRoot 'previews'
New-Item -ItemType Directory -Path $exportRoot,$previewRoot -Force | Out-Null
$jpegCodec = [Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }

function Save-Jpeg([Drawing.Image]$Bitmap, [string]$Path, [long]$Quality = 93) {
    $parameters = [Drawing.Imaging.EncoderParameters]::new(1)
    $parameters.Param[0] = [Drawing.Imaging.EncoderParameter]::new([Drawing.Imaging.Encoder]::Quality, $Quality)
    try { $Bitmap.Save($Path, $jpegCodec, $parameters) } finally { $parameters.Dispose() }
}

function Set-Quality([Drawing.Graphics]$Graphics) {
    $Graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $Graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::HighQuality
    $Graphics.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $Graphics.CompositingQuality = [Drawing.Drawing2D.CompositingQuality]::HighQuality
    $Graphics.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAliasGridFit
}

function Draw-Fitted([Drawing.Graphics]$Graphics, [Drawing.Image]$Source, [Drawing.RectangleF]$Crop, [Drawing.RectangleF]$Box) {
    $fitScale = [Math]::Min($Box.Width / $Crop.Width, $Box.Height / $Crop.Height)
    $fitWidth = [single]($Crop.Width * $fitScale)
    $fitHeight = [single]($Crop.Height * $fitScale)
    $fitRect = [Drawing.RectangleF]::new($Box.X + ($Box.Width-$fitWidth)/2, $Box.Y + ($Box.Height-$fitHeight)/2, $fitWidth, $fitHeight)
    $Graphics.DrawImage($Source, $fitRect, $Crop, [Drawing.GraphicsUnit]::Pixel)
    return $fitRect
}

$bannerSpecs = @(
    @{ Id='github-readme'; File='github-readme-1600x700.jpg'; Width=1600; Height=700; Source='selected-persona-polish.png'; Crop=@(0,0,1894,830); Box=@(0,0,1600,700); Label='GitHub README'; MaxBytes=0; Note='Custom README design size; uniform fit of selected full artwork.' },
    @{ Id='github-social-preview'; File='github-social-preview-1280x640.jpg'; Width=1280; Height=640; Source='selected-persona-polish.png'; Crop=@(0,0,1894,830); Box=@(0,0,1280,640); Label='GitHub social preview'; MaxBytes=1000000; Note='Preserves selected artwork with warm cream vertical breathing room.' },
    @{ Id='x-header'; File='x-header-1500x500.jpg'; Width=1500; Height=500; Source='linkedin-company-frieze.png'; Crop=@(0,0,2172,724); Box=@(0,0,1500,500); Label='X profile header'; MaxBytes=0; Safe=@(0,60,1500,380); Avatar=@(24,335,230,230); Note='Uses shallow frieze to keep all essential content in the middle 380px. Avatar exclusion is a conservative illustrative preview, not a platform guarantee.' },
    @{ Id='linkedin-personal'; File='linkedin-personal-1584x396.jpg'; Width=1584; Height=396; Source='linkedin-personal-panorama.png'; Crop=@(0,25,2172,670); Box=@(270,0,1284,396); Label='LinkedIn personal background'; MaxBytes=8000000; ExtendLeft=$true; Avatar=@(35,172,260,260); Note='Keeps brand in upper middle-left; quiet left area for variable profile-photo overlap. Only empty outer background trimmed.' },
    @{ Id='linkedin-company'; File='linkedin-company-1512x256.jpg'; Width=1512; Height=256; Source='linkedin-company-frieze.png'; Crop=@(0,200,2172,350); Box=@(25,10,1462,236); Label='LinkedIn company Page'; MaxBytes=3000000; ExtendLeft=$true; Note='Very shallow cast arrangement, uniform scaling, cream margins; no content cut. Nameplates retained.' },
    @{ Id='youtube-channel'; File='youtube-channel-2560x1440.jpg'; Width=2560; Height=1440; Source='linkedin-personal-panorama.png'; Crop=@(0,25,2172,670); Box=@(510,510,1540,420); Label='YouTube channel banner'; MaxBytes=6000000; Safe=@(510,510,1540,420); Note='All artwork fits within conservative centered 1540x420 text/logo-safe region. Cream surroundings remain visible on TV.' },
    @{ Id='social-link-preview'; File='social-link-preview-1200x630.jpg'; Width=1200; Height=630; Source='selected-persona-polish.png'; Crop=@(0,0,1894,830); Box=@(0,0,1200,630); Label='General social link preview'; MaxBytes=0; Note='Practical general-purpose link card; not a universal platform requirement.' }
)

$manifest = @()
foreach ($spec in $bannerSpecs) {
    $sourcePath = Join-Path (Join-Path $OutputRoot 'masters') $spec.Source
    $sourceImage = [Drawing.Bitmap]::new($sourcePath)
    $canvas = [Drawing.Bitmap]::new($spec.Width,$spec.Height,[Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $canvas.SetResolution(96,96)
    $graphics = [Drawing.Graphics]::FromImage($canvas)
    try {
        Set-Quality $graphics
        $backgroundColor = $sourceImage.GetPixel(0,0)
        $graphics.Clear($backgroundColor)
        $crop = [Drawing.RectangleF]::new($spec.Crop[0],$spec.Crop[1],$spec.Crop[2],$spec.Crop[3])
        $box = [Drawing.RectangleF]::new($spec.Box[0],$spec.Box[1],$spec.Box[2],$spec.Box[3])
        if ($spec.ExtendLeft) {
            # Continue only the existing quiet cream edge across the extra canvas.
            # The subjects themselves are always scaled uniformly, never stretched.
            $leftStrip = [Drawing.RectangleF]::new(0,$crop.Y,1,$crop.Height)
            $graphics.DrawImage($sourceImage,[Drawing.RectangleF]::new(0,$box.Y,$spec.Width,$box.Height),$leftStrip,[Drawing.GraphicsUnit]::Pixel)
        }
        $destination = Draw-Fitted $graphics $sourceImage $crop $box
        $outputPath = Join-Path $exportRoot $spec.File
        Save-Jpeg $canvas $outputPath
        $written = [Drawing.Image]::FromFile($outputPath)
        try {
            if ($written.Width -ne $spec.Width -or $written.Height -ne $spec.Height) { throw ('Incorrect dimensions: ' + $spec.File) }
        } finally { $written.Dispose() }
        $byteCount = (Get-Item -LiteralPath $outputPath).Length
        if ($spec.MaxBytes -gt 0 -and $byteCount -ge $spec.MaxBytes) { throw ('File exceeds platform limit: ' + $spec.File) }
        $manifest += [ordered]@{ id=$spec.Id; file=('exports/'+$spec.File); width=$spec.Width; height=$spec.Height; bytes=$byteCount; jpeg_quality=93; source=('masters/'+$spec.Source); source_crop=$spec.Crop; destination=@([Math]::Round($destination.X,2),[Math]::Round($destination.Y,2),[Math]::Round($destination.Width,2),[Math]::Round($destination.Height,2)); note=$spec.Note; safe_area=$spec.Safe }
        if ($spec.Safe -or $spec.Avatar) {
            if ($spec.Safe) {
                $safe = $spec.Safe
                $shade = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(100,20,32,40))
                $safePen = [Drawing.Pen]::new([Drawing.Color]::FromArgb(26,134,102),3)
                try {
                    $graphics.FillRectangle($shade,0,0,$spec.Width,$safe[1])
                    $graphics.FillRectangle($shade,0,$safe[1]+$safe[3],$spec.Width,$spec.Height-$safe[1]-$safe[3])
                    $graphics.FillRectangle($shade,0,$safe[1],$safe[0],$safe[3])
                    $graphics.FillRectangle($shade,$safe[0]+$safe[2],$safe[1],$spec.Width-$safe[0]-$safe[2],$safe[3])
                    $graphics.DrawRectangle($safePen,$safe[0],$safe[1],$safe[2],$safe[3])
                } finally { $shade.Dispose(); $safePen.Dispose() }
            }
            if ($spec.Avatar) {
                $avatar = $spec.Avatar
                $avatarBrush = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(225,235,237,239))
                $avatarPen = [Drawing.Pen]::new([Drawing.Color]::White,5)
                try { $graphics.FillEllipse($avatarBrush,$avatar[0],$avatar[1],$avatar[2],$avatar[3]); $graphics.DrawEllipse($avatarPen,$avatar[0],$avatar[1],$avatar[2],$avatar[3]) }
                finally { $avatarBrush.Dispose(); $avatarPen.Dispose() }
            }
            Save-Jpeg $canvas (Join-Path $previewRoot ($spec.Id+'-crop-guide.jpg')) 88
        }
    } finally { $graphics.Dispose(); $canvas.Dispose(); $sourceImage.Dispose() }
}

$manifestPath = Join-Path $OutputRoot 'export-manifest.json'
[IO.File]::WriteAllText($manifestPath,($manifest | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))

# A compact visual index; export JPEGs above contain no guide overlays.
$sheet = [Drawing.Bitmap]::new(2000,1640,[Drawing.Imaging.PixelFormat]::Format24bppRgb)
$sheetGraphics = [Drawing.Graphics]::FromImage($sheet)
$titleFont = [Drawing.Font]::new('Segoe UI',25,[Drawing.FontStyle]::Bold,[Drawing.GraphicsUnit]::Pixel)
$labelFont = [Drawing.Font]::new('Segoe UI',20,[Drawing.FontStyle]::Bold,[Drawing.GraphicsUnit]::Pixel)
$smallFont = [Drawing.Font]::new('Segoe UI',17,[Drawing.FontStyle]::Regular,[Drawing.GraphicsUnit]::Pixel)
$ink = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(40,40,38))
try {
    Set-Quality $sheetGraphics
    $sheetGraphics.Clear([Drawing.Color]::FromArgb(246,244,240))
    $sheetGraphics.DrawString('OpenCompany | Platform banner kit',$titleFont,$ink,30,20)
    $sheetGraphics.DrawString('Exact-size JPEG exports. Selected cat personas, token workflow, customer payment.',$smallFont,$ink,30,60)
    $positions = @(@(30,115,940,410),@(1030,115,940,470),@(30,585,940,320),@(1030,645,940,260),@(30,965,940,180),@(1030,965,940,530),@(30,1205,940,400))
    for ($index=0; $index -lt $bannerSpecs.Count; $index++) {
        $spec = $bannerSpecs[$index]; $position = $positions[$index]
        $sheetGraphics.DrawString(($spec.Label+'  |  '+$spec.Width+' x '+$spec.Height),$labelFont,$ink,$position[0],$position[1]-32)
        $thumb = [Drawing.Image]::FromFile((Join-Path $exportRoot $spec.File))
        try { $null = Draw-Fitted $sheetGraphics $thumb ([Drawing.RectangleF]::new(0,0,$thumb.Width,$thumb.Height)) ([Drawing.RectangleF]::new($position[0],$position[1],$position[2],$position[3])) }
        finally { $thumb.Dispose() }
    }
    Save-Jpeg $sheet (Join-Path $previewRoot 'platform-overview.jpg') 90
} finally { $titleFont.Dispose(); $labelFont.Dispose(); $smallFont.Dispose(); $ink.Dispose(); $sheetGraphics.Dispose(); $sheet.Dispose() }
$manifest | ForEach-Object { [pscustomobject]$_ } | Select-Object id,width,height,bytes | ConvertTo-Json -Compress
