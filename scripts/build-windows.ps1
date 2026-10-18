$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot '../app/scripts/build_windows_v0143.ps1') -BuildTag 'v1.0.0'
