param([ValidateSet('plan-aware-v0143-r3', 'plan-aware-v0143-r4', 'plan-aware-v0143-r5', 'plan-aware-v0143-r6', 'plan-aware-v0143-r7', 'plan-aware-v0143-r8', 'plan-aware-v0143-r9', 'plan-aware-v0143-r10', 'plan-aware-v0143-r11', 'plan-aware-v0143-r12', 'plan-aware-v0143-r13', 'plan-aware-v0143-r14', 'plan-aware-v0143-r15', 'plan-aware-v0143-r16', 'plan-aware-v0143-r17', 'plan-aware-v0143-r18', 'plan-aware-v0143-r19', 'plan-aware-v0143-r20', 'plan-aware-v0143-r21', 'plan-aware-v0143-r22', 'plan-aware-v0143-r23', 'plan-aware-v0143-r24', 'plan-aware-v0143-r25', 'plan-aware-v0143-r26', 'plan-aware-v0143-r27', 'plan-aware-v0143-r28', 'plan-aware-v0143-r29', 'plan-aware-v0143-r30', 'plan-aware-v0143-r31')][string]$BuildTag = 'plan-aware-v0143-r31')

$ErrorActionPreference = 'Stop'
$Project = Split-Path -Parent $PSScriptRoot
$Python = (Get-Command python -ErrorAction Stop).Source
$Tag = $BuildTag
$Revision = $Tag.Split('-')[-1]
$Portable = Join-Path $Project "release\QuotaLab-0.14.3-Windows-Portable-$Revision.zip"
$DistRoot = Join-Path $Project "dist\$Tag"
$Dist = Join-Path $DistRoot 'QuotaLab'
$Work = Join-Path $Project "build\$Tag"
$VersionInfo = if ($Revision -in @('r11', 'r12', 'r13', 'r14', 'r15', 'r16', 'r17', 'r18', 'r19', 'r20', 'r21', 'r22', 'r23', 'r24', 'r25', 'r26', 'r27', 'r28', 'r29', 'r30', 'r31')) { Join-Path $Project "packaging\version_info-v0143-$Revision.txt" } else { Join-Path $Project 'packaging\version_info-v0143.txt' }
$HaveDist = Test-Path -LiteralPath $DistRoot
$HaveWork = Test-Path -LiteralPath $Work
$HavePortable = Test-Path -LiteralPath $Portable
if ($HaveDist -ne $HaveWork) { throw 'INCOMPLETE_EXISTING_BUILD_OUTPUT; preserve it and use a new build tag.' }
if ($HavePortable -and -not ($HaveDist -and $HaveWork)) { throw 'ARCHIVE_EXISTS_WITHOUT_MATCHING_BUILD_SOURCE; refusing to replace it.' }

Push-Location $Project
try {
    $Exe = Join-Path $Dist 'QuotaLab.exe'
    if ($HaveDist) {
        if (-not (Test-Path -LiteralPath $Exe)) { throw 'EXISTING_BUILD_EXE_MISSING' }
    } else {
        & $Python -m PyInstaller --noconfirm --clean --onedir --windowed `
            --specpath $Work --distpath $DistRoot --workpath $Work `
            --icon (Join-Path $Project 'assets\phase.ico') --add-data "$Project\assets;assets" `
            --add-data "$Project\ui_visual.js;." --add-data "$Project\ui_art_direction.js;." `
            --add-data "$Project\ui_task_workbench.js;." `
            --add-data "$Project\ui_preview.html;." --add-data "$Project\styles;styles" `
            --add-data "$Project\ui_orchestration.js;." --add-data "$Project\ui_productization.js;." `
            --add-data "$Project\ui_lifecycle.js;." --add-data "$Project\ui_release.js;." `
            --add-data "$Project\ui_hardening.js;." --add-data "$Project\ui_interaction.js;." `
            --add-data "$Project\ui_evolution.js;." --version-file $VersionInfo `
            --name QuotaLab (Join-Path $Project 'quota_lab.py')
        if ($LASTEXITCODE -ne 0) { throw 'PYINSTALLER_FAILED' }
    }
    if (-not (Test-Path -LiteralPath $Exe)) { throw 'PACKAGED_EXE_MISSING' }
    $License = Join-Path $Project '../../LICENSE'
    if (-not (Test-Path -LiteralPath $License)) { throw 'SOURCE_LICENSE_MISSING' }
    Copy-Item -LiteralPath $License -Destination (Join-Path $Dist 'LICENSE.txt')

    if ($Revision -in @('r25', 'r26', 'r27', 'r28', 'r29', 'r30', 'r31')) {
        Copy-Item -LiteralPath (Join-Path $Project 'docs\USER_GUIDE_ZH.md') -Destination (Join-Path $Dist '使用说明.txt')
    }
    if ($Revision -in @('r27', 'r28', 'r29', 'r30', 'r31')) {
        Copy-Item -LiteralPath (Join-Path $Project 'docs\FIRST_RUN_WALKTHROUGH_ZH.md') -Destination (Join-Path $Dist '首次使用验收.txt')
    }

    $ReleaseRoot = Split-Path -Parent $Portable
    if (-not $HavePortable) {
        if (-not (Test-Path -LiteralPath $ReleaseRoot)) { New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null }
        Compress-Archive -LiteralPath $Dist -DestinationPath $Portable
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Archive = [System.IO.Compression.ZipFile]::OpenRead($Portable)
    try {
        $Entries = @{}
        foreach ($Entry in $Archive.Entries) { $Entries[($Entry.FullName -replace '\\', '/')] = $Entry }
        foreach ($Required in @('QuotaLab/QuotaLab.exe', 'QuotaLab/_internal/ui_visual.js',
            'QuotaLab/_internal/ui_art_direction.js',
            'QuotaLab/_internal/ui_task_workbench.js',
            'QuotaLab/_internal/ui_interaction.js',
            'QuotaLab/_internal/ui_productization.js', 'QuotaLab/_internal/ui_evolution.js',
            'QuotaLab/_internal/styles/art-direction.css', 'QuotaLab/_internal/styles/visual-refinement.css')) {
            if (-not $Entries.ContainsKey($Required)) { throw "PORTABLE_ENTRY_MISSING: $Required" }
        }
        foreach ($Pair in @(
            @{ Entry = 'QuotaLab/_internal/ui_visual.js'; Source = 'ui_visual.js' },
            @{ Entry = 'QuotaLab/_internal/ui_art_direction.js'; Source = 'ui_art_direction.js' },
            @{ Entry = 'QuotaLab/_internal/ui_task_workbench.js'; Source = 'ui_task_workbench.js' },
            @{ Entry = 'QuotaLab/_internal/ui_interaction.js'; Source = 'ui_interaction.js' },
            @{ Entry = 'QuotaLab/_internal/ui_productization.js'; Source = 'ui_productization.js' },
            @{ Entry = 'QuotaLab/_internal/ui_evolution.js'; Source = 'ui_evolution.js' },
            @{ Entry = 'QuotaLab/_internal/styles/art-direction.css'; Source = 'styles\art-direction.css' },
            @{ Entry = 'QuotaLab/_internal/styles/visual-refinement.css'; Source = 'styles\visual-refinement.css' }
        )) {
            $Reader = [System.IO.StreamReader]::new($Entries[$Pair.Entry].Open(), [System.Text.Encoding]::UTF8)
            try { $ArchivedText = $Reader.ReadToEnd() } finally { $Reader.Dispose() }
            $SourceText = Get-Content -LiteralPath (Join-Path $Project $Pair.Source) -Raw -Encoding utf8
            if ($ArchivedText -cne $SourceText) { throw "PACKAGED_SOURCE_MISMATCH: $($Pair.Source)" }
        }
        foreach ($Entry in $Archive.Entries) {
            $Stream = $Entry.Open()
            try { $Buffer = [byte[]]::new(65536); while ($Stream.Read($Buffer, 0, $Buffer.Length) -gt 0) { } }
            finally { $Stream.Dispose() }
        }
    }
    finally { $Archive.Dispose() }

    $ChecksumPath = Join-Path $ReleaseRoot "QuotaLab-0.14.3-SHA256-$Revision.txt"
    $ZipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Portable).Hash.ToLowerInvariant()
    $ExeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Exe).Hash.ToLowerInvariant()
    @(
        "$ZipHash  $(Split-Path -Leaf $Portable)"
        "$ExeHash  QuotaLab/QuotaLab.exe (inside portable ZIP)"
    ) | Set-Content -LiteralPath $ChecksumPath -Encoding ascii

    Write-Output 'ARCHIVE_INTEGRITY=PASS'
    Write-Output "PORTABLE=$Portable"
    Write-Output "EXE=$Exe"
    Write-Output "CHECKSUMS=$ChecksumPath"
    Get-FileHash -Algorithm SHA256 -LiteralPath $Portable, $Exe
}
finally { Pop-Location }
