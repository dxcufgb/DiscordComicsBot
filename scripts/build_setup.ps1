<#
.SYNOPSIS
    Builds <stem>-<version>-setup.exe with Inno Setup from the versioned Windows release zip.

.DESCRIPTION
    Run scripts/build_release.py first; this script expands its <stem>-<version>-windows.zip and
    compiles packaging/windows/ComicsBot.iss into <stem>-<version>-setup.exe in the same output folder.
    <stem> is ComicsBot while the release keeps the file names of 2.3.2 for older updaters (LEGACY_ASSETS in
    build_release.py), comicsbot afterwards. The version is the one stamped
    into the zip (build_release.py --version, from the release tag). Inno Setup 6 is used from PATH or its
    default folder, and installed with Chocolatey when it is missing.

.EXAMPLE
    python scripts/build_release.py --version 1.2.3 --out dist
    ./scripts/build_setup.ps1 -OutDir dist
#>
[CmdletBinding()]
param([string]$OutDir = 'dist')

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$distDir = if ([IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $root $OutDir }

# The versioned zip (not the fixed-name comicsbot-windows.zip); its name gives the stem and the version.
$pattern = '^(?<stem>comicsbot|ComicsBot)-(?<version>\d[^\\/]*)-windows\.zip$'
$zips = @(Get-ChildItem $distDir -Filter '*-windows.zip' | Where-Object { $_.Name -cmatch $pattern })
if ($zips.Count -ne 1) { throw "Expected one <stem>-<version>-windows.zip in $distDir; run scripts/build_release.py first." }
$zip = $zips[0]
$null = $zip.Name -cmatch $pattern
$stem = $Matches['stem']
$version = $Matches['version']

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
$source = Join-Path $staging "$stem-$version"
$outputBase = "$stem-$version-setup"

& $iscc /Q "/DAppVersion=$version" "/DSourceDir=$source" "/DOutputBase=$outputBase" "/O$distDir" `
    (Join-Path $root 'packaging\windows\ComicsBot.iss')
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }
Remove-Item $staging -Recurse -Force
Get-Item (Join-Path $distDir "$outputBase.exe")
