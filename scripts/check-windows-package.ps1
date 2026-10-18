param([Parameter(Mandatory=$true)][string]$ZipPath,[int]$Port=18894)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot '../app/scripts/ci_smoke_portable_v0143.ps1') -ZipPath $ZipPath -ExpectedVersion '1.0.0' -Port $Port
