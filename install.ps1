<#
.SYNOPSIS
    Installs ComicsBot on Windows and makes it start automatically.

.DESCRIPTION
    Run install.bat (double-click, or from a command prompt) from the extracted release folder.
    It installs Python if needed (through winget), copies the bot to the install folder, asks for
    the bot token, installs the dependencies and registers a scheduled task that keeps the bot running.

.EXAMPLE
    install.bat
    Install or upgrade into %LOCALAPPDATA%\ComicsBot and start the bot when you log in.

.EXAMPLE
    install.bat -AtStartup
    Start the bot when Windows starts, even if nobody logs in (run as administrator).

.EXAMPLE
    install.bat -MigrateFrom C:\Users\me\DiscordComicsBot
    Also copy .env and the bot data from an older install, e.g. the git checkout it ran from before.

.EXAMPLE
    install.bat -Restart
    install.bat -Stop
    install.bat -Uninstall [-Purge]
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'ComicsBot'),
    [string]$Token = $env:DISCORD_TOKEN,
    [string]$MigrateFrom,
    [switch]$AtStartup,
    [switch]$Yes,
    [switch]$Restart,
    [switch]$Stop,
    [switch]$Uninstall,
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'
$TaskName = 'ComicsBot'
$SourceDir = $PSScriptRoot
# Run from an installed folder (install.bat -Restart etc.), act on that folder by default.
if (-not $PSBoundParameters.ContainsKey('InstallDir') -and (Test-Path (Join-Path $SourceDir '.venv'))) {
    $InstallDir = $SourceDir
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$AppFiles = @('ComicsBot.py', 'comicsbot', 'requirements.txt', 'start-bot.bat', 'install.ps1', 'install.bat',
              '.env.example', 'README.md', 'VERSION')
$DataFiles = @('.env', 'state.json', 'comics.json')
# Everything shown is also appended here, so a run started from the setup.exe (whose console window
# closes) can still be diagnosed.
$LogFile = Join-Path $InstallDir 'install.log'

function Add-InstallLog([string]$Message) {
    if (Test-Path -LiteralPath $InstallDir -PathType Container) {
        Add-Content -LiteralPath $LogFile -Value "$(Get-Date -Format 's') $Message" -ErrorAction SilentlyContinue
    }
}

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
    Add-InstallLog "==> $Message"
}

function Stop-WithError([string]$Message) {
    Write-Host "Error: $Message" -ForegroundColor Red
    Add-InstallLog "Error: $Message"
    exit 1
}

function Confirm-Step([string]$Question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$Question [Y/n]"
    return ($answer -eq '' -or $answer -match '^[Yy]')
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Runs a native program and returns its exit code without letting stderr output abort the script.
function Invoke-Native([string]$Exe, [string[]]$Arguments, [switch]$ShowOutput) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($ShowOutput) {
            & $Exe @Arguments 2>&1 | ForEach-Object { Write-Host "    $_"; Add-InstallLog "    $_" }
        } else {
            & $Exe @Arguments 2>&1 | Out-Null
        }
        return $LASTEXITCODE
    } catch {
        return 1
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Find-Python {
    $versionCheck = 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
    $candidates = @(
        @{ Exe = 'py'; Args = @('-3') },
        @{ Exe = 'python'; Args = @() },
        @{ Exe = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'); Args = @('-3') }
    )
    $installed = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Directory -Filter 'Python3*' `
        -ErrorAction SilentlyContinue | Sort-Object Name -Descending
    foreach ($dir in $installed) {
        $candidates += @{ Exe = (Join-Path $dir.FullName 'python.exe'); Args = @() }
    }
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        if ((Invoke-Native $candidate.Exe ($candidate.Args + @('-c', $versionCheck))) -eq 0) {
            return $candidate
        }
    }
    return $null
}

function Install-PythonIfMissing {
    $python = Find-Python
    if ($python) { return $python }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Stop-WithError 'Python 3.10 or newer is required. Install it from https://www.python.org/downloads/ and run this again.'
    }
    if (-not (Confirm-Step 'Python 3.10 or newer is required. Install Python 3.13 with winget now?')) {
        Stop-WithError 'Python 3.10 or newer is required.'
    }
    Write-Step 'Installing Python 3.13 (winget)'
    $code = Invoke-Native 'winget' @('install', '--exact', '--id', 'Python.Python.3.13', '--scope', 'user', '--silent',
                                     '--accept-package-agreements', '--accept-source-agreements') -ShowOutput
    if ($code -ne 0) { Write-Host "winget exited with code $code" }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $python = Find-Python
    if (-not $python) {
        Stop-WithError 'Python was not found after installing it. Open a new window and run install.bat again.'
    }
    return $python
}

function Stop-ProcessTree([int]$ProcessId) {
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-ProcessTree $_.ProcessId }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Get-ProcessAncestry {
    $ids = @()
    $current = $PID
    while ($current -and $ids -notcontains $current) {
        $ids += $current
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $current" -ErrorAction SilentlyContinue
        $current = if ($process) { [int]$process.ParentProcessId } else { 0 }
    }
    return $ids
}

function Stop-Bot {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    # Stopping the task does not always stop the processes it started; end them explicitly.
    $loop = Join-Path $InstallDir 'start-bot.bat'
    $venv = Join-Path $InstallDir '.venv'
    # Never stop this installer itself or the console it runs in (e.g. "start-bot.bat install").
    $own = Get-ProcessAncestry
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($own -notcontains [int]$_.ProcessId) -and (
            ($_.Name -eq 'cmd.exe' -and $_.CommandLine -and
                $_.CommandLine.IndexOf($loop, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
            ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($venv, [StringComparison]::OrdinalIgnoreCase)))
    }
    foreach ($process in $processes) {
        Stop-ProcessTree $process.ProcessId
    }
    if ($processes) { Start-Sleep -Seconds 2 }
}

function Write-TextFile([string]$Path, [string]$Text) {
    # python-dotenv does not expect a byte-order mark, which Set-Content -Encoding UTF8 would add.
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding $false))
}

function Set-EnvToken([string]$EnvFile, [string]$Value) {
    $lines = [IO.File]::ReadAllLines($EnvFile) | ForEach-Object {
        if ($_ -match '^DISCORD_TOKEN=') { "DISCORD_TOKEN=$Value" } else { $_ }
    }
    Write-TextFile $EnvFile (($lines -join "`r`n") + "`r`n")
}

# Only the current user may read the token: drop inherited permissions, grant just this user,
# and check the result instead of trusting that it worked.
function Protect-SecretFile([string]$Path) {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleSpecific($rule) }
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')))
    Set-Acl -LiteralPath $Path -AclObject $acl
    $others = @((Get-Acl -LiteralPath $Path).Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -ne $sid
    })
    if ($others) {
        Stop-WithError "Could not restrict $Path to the current user; still readable by: $($others.IdentityReference -join ', ')"
    }
}

function Read-Token {
    Write-Host 'Paste the bot token from https://discord.com/developers/applications (Bot -> Reset Token).'
    $secure = Read-Host 'Discord bot token' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Register-BotTask {
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $loop = (Join-Path $InstallDir 'start-bot.bat').Replace("'", "''")
    # PowerShell with a hidden window runs the restart loop without leaving a console window open.
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $InstallDir `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& '$loop'`""
    if ($AtStartup) {
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $trigger.Delay = 'PT30S'
        # S4U runs the task without anyone logged in and without storing a password.
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    } else {
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    }
    # No time limit: by default Windows stops scheduled tasks after 3 days.
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Description 'Discord ComicsBot' -Force | Out-Null
}

function Wait-ForLogin([string]$LogFile, [long]$Offset) {
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if (-not (Test-Path $LogFile)) { continue }
        $stream = [IO.File]::Open($LogFile, 'Open', 'Read', 'ReadWrite')
        try {
            if ($stream.Length -lt $Offset) { $Offset = 0 }  # the log was rotated
            [void]$stream.Seek($Offset, 'Begin')
            $text = (New-Object IO.StreamReader($stream)).ReadToEnd()
        } finally {
            $stream.Dispose()
        }
        $match = [regex]::Matches($text, '.*Logged in as.*') | Select-Object -Last 1
        if ($match) { return $match.Value.Trim() }
        $failure = [regex]::Matches($text, '.*(ERROR|CRITICAL).*') | Select-Object -Last 1
        if ($failure) { Stop-WithError "The bot reported an error: $($failure.Value.Trim())" }
    }
    return $null
}

function Install-Bot {
    if (-not (Test-Path (Join-Path $SourceDir 'ComicsBot.py'))) {
        Stop-WithError 'Run install.bat from the extracted release folder.'
    }
    if ($AtStartup -and -not (Test-Administrator)) {
        Stop-WithError '-AtStartup needs administrator rights: right-click install.bat and choose "Run as administrator".'
    }
    $python = Install-PythonIfMissing
    Write-Step "Using Python: $($python.Exe) $($python.Args -join ' ')"
    Stop-Bot

    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    $inPlace = [IO.Path]::GetFullPath($SourceDir).TrimEnd('\') -ieq $InstallDir
    if (-not $inPlace) {
        Write-Step "Copying program files to $InstallDir"
        Remove-Item (Join-Path $InstallDir 'comicsbot') -Recurse -Force -ErrorAction SilentlyContinue
        foreach ($item in $AppFiles) {
            $source = Join-Path $SourceDir $item
            if (Test-Path $source) { Copy-Item $source -Destination $InstallDir -Recurse -Force }
        }
    }

    if ($MigrateFrom) {
        if (-not (Test-Path $MigrateFrom -PathType Container)) { Stop-WithError "-MigrateFrom: $MigrateFrom is not a folder" }
        Write-Step "Copying settings and data from $MigrateFrom"
        $files = @($DataFiles | ForEach-Object { Join-Path $MigrateFrom $_ } | Where-Object { Test-Path $_ -PathType Leaf })
        $files += @(Get-ChildItem $MigrateFrom -Filter '*-Comics.json' -File | ForEach-Object { $_.FullName })
        foreach ($file in $files) {
            $name = Split-Path $file -Leaf
            if (Test-Path (Join-Path $InstallDir $name)) {
                Write-Host "    $name already exists in $InstallDir, kept the existing one"
            } else {
                Copy-Item $file -Destination $InstallDir
                Write-Host "    $name"
            }
        }
    }

    $envFile = Join-Path $InstallDir '.env'
    Write-Step "Writing the settings to $envFile"
    if (-not (Test-Path $envFile)) {
        Copy-Item (Join-Path $InstallDir '.env.example') $envFile
        if (-not $Token) { $Token = Read-Token }
        if (-not $Token) { Stop-WithError 'A bot token is required (-Token or the DISCORD_TOKEN variable).' }
        Set-EnvToken $envFile $Token
    } elseif ($Token) {
        Set-EnvToken $envFile $Token
    }
    Protect-SecretFile $envFile

    $venvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Write-Step 'Creating the Python virtual environment'
        $code = Invoke-Native $python.Exe ($python.Args + @('-m', 'venv', (Join-Path $InstallDir '.venv')))
        if ($code -ne 0) { Stop-WithError 'Could not create the virtual environment.' }
    }
    Write-Step 'Installing Python dependencies'
    $code = Invoke-Native (Join-Path $InstallDir 'start-bot.bat') @('setup') -ShowOutput
    if ($code -ne 0) { Stop-WithError 'Installing the dependencies failed; run start-bot.bat setup to see why.' }

    Write-Step 'Registering the scheduled task'
    Register-BotTask
    $logFile = Join-Path $InstallDir 'discord.log'
    $offset = 0
    if (Test-Path $logFile) { $offset = (Get-Item $logFile).Length }
    Start-ScheduledTask -TaskName $TaskName

    Write-Step 'Waiting for the bot to log in'
    $login = Wait-ForLogin $logFile $offset
    if (-not $login) {
        Stop-WithError "The bot did not report a login within 45 seconds. Check $logFile."
    }
    Write-Host $login
    Write-Host ''
    $version = ''
    $versionFile = Join-Path $InstallDir 'VERSION'
    if (Test-Path $versionFile) { $version = (Get-Content $versionFile -Raw).Trim() }
    $when = if ($AtStartup) { 'when Windows starts' } else { 'when you log in' }
    Write-Host "ComicsBot $version is installed in $InstallDir and starts $when." -ForegroundColor Green
    Write-Host "  Restart:   install.bat -Restart"
    Write-Host "  Stop:      install.bat -Stop"
    Write-Host "  Logs:      $logFile"
    Write-Host "  Settings:  $envFile"
}

function Uninstall-Bot {
    Stop-Bot
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Write-Step 'Removing the scheduled task'
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    if (-not (Test-Path $InstallDir)) { Write-Step "Nothing installed in $InstallDir"; return }
    Set-Location $env:TEMP
    if ($Purge) {
        Write-Step "Deleting $InstallDir"
        Remove-Item $InstallDir -Recurse -Force
    } else {
        Write-Step "Removing program files (keeping .env and data in $InstallDir)"
        foreach ($item in ($AppFiles + '.venv')) {
            if ($item -eq '.env.example') { continue }
            Remove-Item (Join-Path $InstallDir $item) -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Step 'ComicsBot has been uninstalled'
}

try {
    if ($Uninstall) {
        Uninstall-Bot
    } elseif ($Stop) {
        Stop-Bot
        Write-Step 'ComicsBot stopped (it starts again at the next logon/startup, or with install.bat -Restart)'
    } elseif ($Restart) {
        if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) { Stop-WithError 'ComicsBot is not installed.' }
        Stop-Bot
        Start-ScheduledTask -TaskName $TaskName
        Write-Step 'ComicsBot restarted'
    } else {
        Install-Bot
    }
} catch {
    # Unexpected errors would otherwise only reach a console window that may already be gone.
    Stop-WithError "$($_.Exception.Message)`n$($_.InvocationInfo.PositionMessage)"
}
