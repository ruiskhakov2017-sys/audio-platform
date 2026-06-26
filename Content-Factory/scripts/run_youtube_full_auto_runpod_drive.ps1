param(
    [string]$LaunchId = "",
    [string]$SiteRunId = "auto",
    [string]$Story = "",
    [string]$ResetStoryArtifactsFromLaunch = "",
    [int]$Limit = 0,
    [int]$TargetYes = 0,
    [string]$FramesRunpodUrl = "",
    [int]$MinWords = 0,
    [int]$MaxWords = 0,
    [switch]$ForceSelectionYes,
    [switch]$RunYoutubeSelection,
    [string]$PodSsh = "",
    [string]$PodHost = "",
    [int]$PodPort = 0,
    [string]$Identity = "$env:USERPROFILE\.ssh\id_ed25519",
    [string]$RcloneConfig = ".\secrets\rclone.conf",
    [string]$DriveRemote = "gdrive",
    [string]$DrivePath = "",
    [int]$GeminiWorkers = 0,
    [string]$GeminiAccounts = "",
    [int]$TtsWorkers = 5,
    [double]$TtsPollMinutes = 1.0,
    [double]$TtsMaxHours = 12.0,
    [string]$TtsStartCmd = ".\START_YOUTUBE_TTS_YANDEX_5TABS_PROFILE_PROXY.bat",
    [switch]$TtsNoStartBrowser,
    [switch]$SkipTtsWaitImport,
    [int]$VideoWorkers = 4,
    [int]$DriveParallelUploads = 4,
    [string]$DriveChunkSize = "256M",
    [int]$UploadChunkSizeMb = 32,
    [int]$PollSeconds = 60,
    [bool]$CleanRemoteIntermediatesAfterDriveUpload = $true,
    [switch]$CleanupBrowsersBeforeRun,
    [switch]$FreshLaunch,
    [switch]$SkipPackageStage,
    [switch]$SkipVideoStart,
    [switch]$SkipDriveUpload,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$rclone = Join-Path $projectRoot "tools\rclone\rclone.exe"
$packageStage = Join-Path $PSScriptRoot "youtube_full_auto_package_stage.py"
$resetStory = Join-Path $PSScriptRoot "reset_youtube_story_for_full_test.py"
$runpodAuto = Join-Path $PSScriptRoot "run_youtube_runpod_autonomous.ps1"

function Resolve-ProjectPath([string]$PathValue) {
    if ($PathValue.StartsWith("~")) {
        return ($PathValue -replace "^~", $HOME)
    }
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }
    return (Join-Path $projectRoot $PathValue)
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments, [string]$StepName) {
    Write-Host "AUTO_STEP_STARTED step=$StepName"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "step failed: $StepName exit=$LASTEXITCODE"
    }
    Write-Host "AUTO_STEP_DONE step=$StepName"
}

function Import-PodSsh([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }
    $text = $Value.Trim()
    if ($text -match "root@([^\s]+)") {
        $script:PodHost = $Matches[1].Trim().Trim('"').Trim("'")
    } elseif ($text -match "^([^:\s]+):(\d+)$") {
        $script:PodHost = $Matches[1].Trim()
        $script:PodPort = [int]$Matches[2]
    } elseif ($text -match "^[0-9A-Za-z_.-]+$") {
        $script:PodHost = $text
    }
    if ($text -match "(?:^|\s)-p\s+(\d+)") {
        $script:PodPort = [int]$Matches[1]
    }
    if ($text -match "(?:^|\s)-i\s+`"([^`"]+)`"") {
        $script:Identity = $Matches[1]
    } elseif ($text -match "(?:^|\s)-i\s+'([^']+)'") {
        $script:Identity = $Matches[1]
    } elseif ($text -match "(?:^|\s)-i\s+([^\s]+)") {
        $script:Identity = $Matches[1]
    }
}

Set-Location $projectRoot

if ([string]::IsNullOrWhiteSpace($LaunchId)) {
    $LaunchId = "YT_AUTO_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
}
if ([string]::IsNullOrWhiteSpace($DrivePath)) {
    $DrivePath = "ContentFactory_YouTube/$LaunchId"
}

if ($FreshLaunch) {
    $launchRoot = Join-Path (Join-Path $projectRoot "Запуски") $LaunchId
    if (Test-Path -LiteralPath $launchRoot) {
        $resolvedLaunch = (Resolve-Path -LiteralPath $launchRoot).Path
        $allowedRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot "Запуски")).Path
        if (-not $resolvedLaunch.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "FreshLaunch refused unsafe path: $resolvedLaunch"
        }
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $quarantineRoot = Join-Path $allowedRoot "_quarantine"
        New-Item -ItemType Directory -Force $quarantineRoot | Out-Null
        $dest = Join-Path $quarantineRoot "${LaunchId}_fresh_${stamp}"
        Move-Item -LiteralPath $resolvedLaunch -Destination $dest
        Write-Host "FRESH_LAUNCH_MOVED_OLD launch_id=$LaunchId dest=$dest"
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python runtime missing: $python"
}
if (-not (Test-Path -LiteralPath $packageStage)) {
    throw "Package-stage runner missing: $packageStage"
}
if (-not (Test-Path -LiteralPath $resetStory)) {
    throw "Story reset runner missing: $resetStory"
}
if (-not (Test-Path -LiteralPath $runpodAuto)) {
    throw "RunPod autonomous runner missing: $runpodAuto"
}
if (-not (Test-Path -LiteralPath $rclone)) {
    throw "rclone missing: $rclone"
}

$RcloneConfig = Resolve-ProjectPath $RcloneConfig
if (-not (Test-Path -LiteralPath $RcloneConfig)) {
    $appRcloneConfig = Join-Path $env:APPDATA "rclone\rclone.conf"
    if (Test-Path -LiteralPath $appRcloneConfig) {
        New-Item -ItemType Directory -Force (Split-Path -Parent $RcloneConfig) | Out-Null
        Copy-Item -LiteralPath $appRcloneConfig -Destination $RcloneConfig -Force
        Write-Host "RCLONE_CONFIG_COPIED path=$RcloneConfig"
    } else {
        throw "Google Drive rclone config missing: $RcloneConfig"
    }
}

Write-Host "GOOGLE_DRIVE_CHECK_STARTED remote=$DriveRemote config=$RcloneConfig"
& $rclone --config $RcloneConfig lsd "${DriveRemote}:" *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Google Drive check failed for remote '$DriveRemote'. Run rclone config once, then restart this script."
}
Write-Host "GOOGLE_DRIVE_READY remote=$DriveRemote path=$DrivePath"

$geminiWorkersLabel = if ($GeminiWorkers -gt 0) { "$GeminiWorkers" } else { "auto" }
$geminiAccountsLabel = if (-not [string]::IsNullOrWhiteSpace($GeminiAccounts)) { $GeminiAccounts } else { "auto" }
$selectionMode = if ($RunYoutubeSelection) { "gemini_selection" } else { "already_approved_skip_selection" }
Write-Host "AUTO_RUN_PREPARED launch_id=$LaunchId drive=${DriveRemote}:$DrivePath selection_mode=$selectionMode gemini_workers=$geminiWorkersLabel gemini_accounts=$geminiAccountsLabel video_workers=$VideoWorkers drive_uploads=$DriveParallelUploads"

if (-not [string]::IsNullOrWhiteSpace($ResetStoryArtifactsFromLaunch)) {
    if ([string]::IsNullOrWhiteSpace($Story)) {
        throw "-ResetStoryArtifactsFromLaunch requires -Story"
    }
    $resetArgs = @(
        $resetStory,
        "--launch-id", $ResetStoryArtifactsFromLaunch,
        "--story", $Story,
        "--execute"
    )
    Invoke-Checked $python $resetArgs "reset_story_artifacts"
}

if (-not $SkipPackageStage) {
    $packageArgs = @(
        $packageStage,
        "--launch-id", $LaunchId,
        "--site-run-id", $SiteRunId,
        "--story", $Story,
        "--limit", "$Limit",
        "--target-yes", "$TargetYes",
        "--min-words", "$MinWords",
        "--max-words", "$MaxWords",
        "--tts-workers", "$TtsWorkers",
        "--tts-poll-minutes", "$TtsPollMinutes",
        "--tts-max-hours", "$TtsMaxHours",
        "--tts-start-cmd", $TtsStartCmd
    )
    if (-not [string]::IsNullOrWhiteSpace($FramesRunpodUrl)) {
        $packageArgs += @("--frames-runpod-url", $FramesRunpodUrl)
    }
    if ($GeminiWorkers -gt 0) {
        $packageArgs += @("--gemini-workers", "$GeminiWorkers")
    }
    if (-not [string]::IsNullOrWhiteSpace($GeminiAccounts)) {
        $packageArgs += @("--gemini-accounts", $GeminiAccounts)
    }
    if ($CleanupBrowsersBeforeRun) {
        $packageArgs += "--cleanup-browsers-before-run"
    }
    if ($ForceSelectionYes -or (-not $RunYoutubeSelection)) {
        $packageArgs += "--force-selection-yes"
    }
    if ($TtsNoStartBrowser) {
        $packageArgs += "--tts-no-start-browser"
    }
    if ($SkipTtsWaitImport) {
        $packageArgs += "--skip-tts-wait-import"
    }
    Invoke-Checked $python $packageArgs "package_stage"
}

if ((-not $SkipVideoStart) -or (-not $SkipDriveUpload)) {
    Import-PodSsh $PodSsh
    if ([string]::IsNullOrWhiteSpace($PodHost)) {
        $podInput = Read-Host "Enter video RunPod host/IP or full ssh command"
        Import-PodSsh $podInput
    }
    while ($PodPort -le 0) {
        $rawPort = Read-Host "Enter video RunPod SSH port"
        [int]$parsedPort = 0
        if ([int]::TryParse($rawPort, [ref]$parsedPort) -and $parsedPort -gt 0) {
            $PodPort = $parsedPort
        } else {
            Write-Host "PORT_INVALID value=$rawPort"
        }
    }
    $Identity = Resolve-ProjectPath $Identity
    if (-not (Test-Path -LiteralPath $Identity)) {
        throw "SSH key missing: $Identity"
    }
    Write-Host "VIDEO_RUNPOD_READY host=$PodHost port=$PodPort"
}

if (-not $SkipVideoStart) {
    $startArgs = @(
        "-Action", "start",
        "-LaunchId", $LaunchId,
        "-PodHost", $PodHost,
        "-PodPort", "$PodPort",
        "-Identity", $Identity,
        "-Workers", "$VideoWorkers",
        "-UploadChunkSizeMb", "$UploadChunkSizeMb",
        "-PollSeconds", "$PollSeconds"
    )
    if (-not [string]::IsNullOrWhiteSpace($Story)) {
        $startArgs += @("-OnlyStories", $Story)
    }
    Invoke-Checked $runpodAuto $startArgs "runpod_video_start"
}

if (-not $SkipDriveUpload) {
    $driveArgs = @(
        "-Action", "watch-drive-upload",
        "-LaunchId", $LaunchId,
        "-PodHost", $PodHost,
        "-PodPort", "$PodPort",
        "-Identity", $Identity,
        "-RcloneConfig", $RcloneConfig,
        "-DriveRemote", $DriveRemote,
        "-DrivePath", $DrivePath,
        "-DriveParallelUploads", "$DriveParallelUploads",
        "-DriveChunkSize", $DriveChunkSize,
        "-PollSeconds", "$PollSeconds"
    )
    if ($CleanRemoteIntermediatesAfterDriveUpload) {
        $driveArgs += "-CleanRemoteIntermediatesAfterDriveUpload"
    }
    Invoke-Checked $runpodAuto $driveArgs "drive_upload_watch"
}

if ($NoWait -or $SkipDriveUpload) {
    Write-Host "AUTO_RUN_BACKGROUND_STARTED launch_id=$LaunchId drive=${DriveRemote}:$DrivePath"
    exit 0
}

Write-Host "AUTO_WAIT_STARTED launch_id=$LaunchId"
while ($true) {
    Start-Sleep -Seconds ([Math]::Max(15, $PollSeconds))
    $statusArgs = @(
        "-Action", "drive-status",
        "-LaunchId", $LaunchId,
        "-PodHost", $PodHost,
        "-PodPort", "$PodPort",
        "-Identity", $Identity,
        "-Tail", "120"
    )
    $statusText = (& $runpodAuto @statusArgs 2>&1) -join "`n"
    Write-Host $statusText
    if ($statusText -match "failed=([1-9][0-9]*)" -and $statusText -match "DRIVE_UPLOAD_LOOP_FINISHED") {
        throw "Drive upload finished with failed files. Check drive-status log above."
    }
    if ($statusText -match "DRIVE_UPLOAD_LOOP_FINISHED") {
        Write-Host "AUTO_RUN_DONE launch_id=$LaunchId drive=${DriveRemote}:$DrivePath"
        break
    }
}
