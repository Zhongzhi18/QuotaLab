param(
    [Parameter(Mandatory = $true)][string]$ZipPath,
    [string]$ExpectedVersion = '0.14.3-plan-aware-preview',
    [ValidateRange(1024, 65535)][int]$Port = 18894
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ResolvedZip = (Resolve-Path -LiteralPath $ZipPath).Path
$TempBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
if (-not $TempBase.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
    $TempBase += [System.IO.Path]::DirectorySeparatorChar
}
$RunRoot = [System.IO.Path]::GetFullPath((Join-Path $TempBase "QuotaLab-r31-smoke-$PID-$([guid]::NewGuid().ToString('N'))"))
if (-not $RunRoot.StartsWith($TempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'TEMP_PATH_GUARD_REJECTED'
}

$OldDataRoot = $env:QUOTALAB_DATA_ROOT
$OldTestScope = $env:QUOTALAB_TEST_SCOPE
$Process = $null
New-Item -ItemType Directory -Path $RunRoot | Out-Null
try {
    $ExtractRoot = Join-Path $RunRoot 'extracted'
    $DataRoot = Join-Path $RunRoot 'isolated-data'
    New-Item -ItemType Directory -Path $ExtractRoot, $DataRoot | Out-Null
    Expand-Archive -LiteralPath $ResolvedZip -DestinationPath $ExtractRoot

    $Exe = Join-Path $ExtractRoot 'QuotaLab\QuotaLab.exe'
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) {
        throw 'PORTABLE_EXECUTABLE_NOT_FOUND'
    }
    if ([Diagnostics.FileVersionInfo]::GetVersionInfo($Exe).FileVersion -ne '0.14.3.31') {
        throw 'PORTABLE_FILE_VERSION_MISMATCH'
    }

    $env:QUOTALAB_DATA_ROOT = $DataRoot
    $env:QUOTALAB_TEST_SCOPE = "ci-mock-portable-$PID"
    $Process = Start-Process -FilePath $Exe `
        -ArgumentList @('start', '--mock', '--no-browser', '--port', "$Port") `
        -WorkingDirectory (Split-Path -Parent $Exe) -WindowStyle Hidden -PassThru

    $Health = $null
    for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
        if ($Process.HasExited) { throw "PORTABLE_PROCESS_EXITED_EARLY: $($Process.ExitCode)" }
        try {
            $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $Health) { throw 'PORTABLE_LOCAL_HEALTH_TIMEOUT' }
    if ($Health.status -ne 'ok' -or $Health.mode -ne 'mock' -or $Health.version -ne $ExpectedVersion) {
        throw 'PORTABLE_HEALTH_CONTRACT_MISMATCH'
    }

    $Page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/" -TimeoutSec 5
    if ($Page.StatusCode -ne 200 -or $Page.Content -notmatch '<title>QuotaLab') {
        throw 'PORTABLE_DASHBOARD_LOAD_FAILED'
    }
    if ($Page.Headers['X-Frame-Options'] -ne 'DENY' -or
        $Page.Headers['Referrer-Policy'] -ne 'no-referrer' -or
        $Page.Headers['Content-Security-Policy'] -notmatch "default-src 'self'") {
        throw 'PORTABLE_SECURITY_HEADER_MISSING'
    }

    $Status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 5
    if ($Status.app.version -ne $ExpectedVersion -or $Status.app.mode -ne 'mock') {
        throw 'PORTABLE_STATUS_CONTRACT_MISMATCH'
    }
    if ($null -eq $Status.desktopNotifications -or
        $Status.desktopNotifications.enabled -ne $false -or
        $Status.desktopNotifications.supported -ne $false -or
        $Page.Content -notmatch 'function enhanceTaskWorkbench') {
        throw 'PORTABLE_TASK_WORKBENCH_OR_NOTIFICATION_DEFAULT_MISMATCH'
    }
    if (-not (Test-Path -LiteralPath $DataRoot -PathType Container)) {
        throw 'ISOLATED_DATA_ROOT_NOT_CREATED'
    }

    Write-Output 'PORTABLE_EXTRACT=PASS'
    Write-Output 'MOCK_LOCAL_SERVICE=PASS'
    Write-Output 'DASHBOARD_AND_SECURITY_HEADERS=PASS'
    Write-Output 'TASK_WORKBENCH_AND_SAFE_NOTIFICATION_DEFAULTS=PASS'
    Write-Output 'REAL_TASKS_SENT=0'
}
finally {
    if ($Process -and -not $Process.HasExited) {
        try {
            Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/shutdown" `
                -ContentType 'application/json' -Body '{}' -TimeoutSec 2 | Out-Null
        } catch { }
        try {
            if (-not $Process.WaitForExit(5000)) { Stop-Process -Id $Process.Id -Force }
        } catch { }
    }

    if ($null -eq $OldDataRoot) { Remove-Item Env:\QUOTALAB_DATA_ROOT -ErrorAction SilentlyContinue }
    else { $env:QUOTALAB_DATA_ROOT = $OldDataRoot }
    if ($null -eq $OldTestScope) { Remove-Item Env:\QUOTALAB_TEST_SCOPE -ErrorAction SilentlyContinue }
    else { $env:QUOTALAB_TEST_SCOPE = $OldTestScope }

    if (Test-Path -LiteralPath $RunRoot) {
        $ResolvedRunRoot = [System.IO.Path]::GetFullPath($RunRoot)
        if (-not $ResolvedRunRoot.StartsWith($TempBase, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'TEMP_CLEANUP_PATH_GUARD_REJECTED'
        }
        Remove-Item -LiteralPath $ResolvedRunRoot -Recurse -Force
    }
}
