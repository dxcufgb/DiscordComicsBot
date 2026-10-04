<#
.SYNOPSIS
    Builds ComicsBot-<version>-setup.exe with Inno Setup from the Windows release zip.

.DESCRIPTION
    Run scripts/build_release.py first; this script expands its ComicsBot-<version>-windows.zip and
    compiles packaging/windows/ComicsBot.iss into the same output folder. Inno Setup 6 is used from
    PATH or its default folder, and installed with Chocolatey when it is missing.

.EXAMPLE
    python scripts/build_release.py --out dist
    ./scripts/build_setup.ps1 -OutDir dist
#>
[CmdletBinding()]
param([string]$OutDir = 'dist')

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$distDir = if ([IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $root $OutDir }

$zip = Get-ChildItem $distDir -Filter 'ComicsBot-*-windows.zip' | Select-Object -First 1
if (-not $zip) { throw "No ComicsBot-*-windows.zip in $distDir; run scripts/build_release.py first." }
$version = $zip.Name -replace '^ComicsBot-(.+)-windows\.zip$', '$1'

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    Write-Output 'Inno Setup not found; installing it with Chocolatey'
    choco install innosetup -y --no-progress | Out-Host
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}

$staging = Join-Path ([IO.Path]::GetTempPath()) "comicsbot-setup-$version"
Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive $zip.FullName -DestinationPath $staging
$source = Join-Path $staging "ComicsBot-$version"

& $iscc /Q "/DAppVersion=$version" "/DSourceDir=$source" "/O$distDir" (Join-Path $root 'packaging\windows\ComicsBot.iss')
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }
Remove-Item $staging -Recurse -Force
Get-Item (Join-Path $distDir "ComicsBot-$version-setup.exe")
