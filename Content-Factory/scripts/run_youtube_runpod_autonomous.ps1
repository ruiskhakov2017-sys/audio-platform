param(
    [ValidateSet("start", "upload-only", "status", "stop", "download", "watch-download", "drive-upload", "watch-drive-upload", "drive-stop", "drive-status", "drive-clean-uploaded")]
    [string]$Action = "status",
    [string]$LaunchId = "YT_PRE_RUNPOD_PROD_02",
    [Parameter(Mandatory = $true)]
    [string]$PodHost,
    [Parameter(Mandatory = $true)]
    [int]$PodPort,
    [string]$Identity = "$env:USERPROFILE\.ssh\id_ed25519",
    [int]$Limit = 0,
    [string]$OnlyStories = "",
    [int]$Tail = 50,
    [int]$ParallelDownloads = 8,
    [int]$ChunkSizeMb = 128,
    [int]$UploadChunkSizeMb = 32,
    [int]$PollSeconds = 120,
    [switch]$DeleteRemoteAfterDownload,
    [string]$RcloneConfig = "",
    [string]$DriveRemote = "gdrive",
    [string]$DrivePath = "",
    [string]$DriveChunkSize = "256M",
    [int]$DriveParallelUploads = 2,
    [switch]$DeleteRemoteAfterDriveUpload,
    [switch]$CleanRemoteIntermediatesAfterDriveUpload,
    [int]$Workers = 4
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$controller = Join-Path $PSScriptRoot "youtube_runpod_autonomous.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python runtime missing: $python"
}
if (-not (Test-Path -LiteralPath $Identity)) {
    throw "SSH key missing: $Identity"
}

Set-Location $projectRoot

$argsList = @(
    $controller,
    $Action,
    "--launch-id", $LaunchId,
    "--pod-host", $PodHost,
    "--pod-port", "$PodPort",
    "--identity", $Identity,
    "--limit", "$Limit",
    "--tail", "$Tail",
    "--parallel-downloads", "$ParallelDownloads",
    "--chunk-size-mb", "$ChunkSizeMb",
    "--upload-chunk-size-mb", "$UploadChunkSizeMb",
    "--poll-seconds", "$PollSeconds",
    "--drive-parallel-uploads", "$DriveParallelUploads",
    "--workers", "$Workers"
)

if (-not [string]::IsNullOrWhiteSpace($OnlyStories)) {
    $argsList += @("--only-stories", $OnlyStories)
}
if (-not [string]::IsNullOrWhiteSpace($RcloneConfig)) {
    $argsList += @("--rclone-config", $RcloneConfig)
}
if (-not [string]::IsNullOrWhiteSpace($DriveRemote)) {
    $argsList += @("--drive-remote", $DriveRemote)
}
if (-not [string]::IsNullOrWhiteSpace($DrivePath)) {
    $argsList += @("--drive-path", $DrivePath)
}
if (-not [string]::IsNullOrWhiteSpace($DriveChunkSize)) {
    $argsList += @("--drive-chunk-size", $DriveChunkSize)
}
if ($DeleteRemoteAfterDownload) {
    $argsList += "--delete-remote-after-download"
}
if ($DeleteRemoteAfterDriveUpload) {
    $argsList += "--delete-remote-after-drive-upload"
}
if ($CleanRemoteIntermediatesAfterDriveUpload) {
    $argsList += "--clean-remote-intermediates-after-drive-upload"
}

& $python @argsList
exit $LASTEXITCODE
