param([string]$OutputRoot = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $OutputRoot).Path
$archivePath = Join-Path $resolvedRoot 'opencompany-platform-banners.zip'
$inputNames = @('exports','previews','masters','prompts','concepts','README.md','export.ps1','package.ps1','generation-plan.json','generation-record.json','export-manifest.json')
$archiveInputs = foreach ($inputName in $inputNames) {
    $inputPath = Join-Path $resolvedRoot $inputName
    if (-not (Test-Path -LiteralPath $inputPath)) { throw ('Missing package input: ' + $inputName) }
    $inputPath
}
Compress-Archive -LiteralPath $archiveInputs -DestinationPath $archivePath -CompressionLevel Optimal -Force
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    $records = Get-Content -LiteralPath (Join-Path $resolvedRoot 'export-manifest.json') -Raw | ConvertFrom-Json
    foreach ($record in $records) {
        $entry = $archive.Entries | Where-Object { $_.FullName.Replace('\','/') -eq $record.file }
        if (-not $entry -or $entry.Length -ne $record.bytes) { throw ('Missing or changed export in package: ' + $record.file) }
    }
    [pscustomobject]@{ Archive=$archivePath; VerifiedExports=$records.Count; Bytes=(Get-Item -LiteralPath $archivePath).Length }
} finally { $archive.Dispose() }
