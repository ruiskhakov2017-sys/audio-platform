# Restore files: for each file in TARGET, find same name in SOURCE and copy over.
$ErrorActionPreference = 'Stop'
$target = $env:TARGET_DIR
$source = $env:SOURCE_DIR

if (-not (Test-Path -LiteralPath $target -PathType Container)) {
  Write-Host "ERROR: Folder not found: $target"
  exit 1
}
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
  Write-Host "ERROR: Folder not found: $source"
  exit 1
}

$targetFiles = Get-ChildItem -LiteralPath $target -Recurse -File
$count = 0
$notFound = 0

foreach ($f in $targetFiles) {
  $name = $f.Name
  # If name ends with _M or _F before extension (voice-over suffix), search for same file without that suffix
  $searchName = if ($name -match '_[MF]\.[^.]+$') { $name -replace '_[MF](\.[^.]+)$', '$1' } else { $name }
  $src = Get-ChildItem -LiteralPath $source -Recurse -Filter $searchName -File -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($src) {
    try {
      Copy-Item -LiteralPath $src.FullName -Destination $f.FullName -Force
      Write-Host "OK: $($f.FullName)"
      $count++
    } catch {
      Write-Host "FAILED: $($f.FullName) - $_"
    }
  } else {
    $notFound++
  }
}

Write-Host ""
Write-Host "Done. Replaced: $count. Not found in source: $notFound"
