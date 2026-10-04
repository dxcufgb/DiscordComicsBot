@echo off
REM ComicsBot installer for Windows. Double-click it, or run "install.bat -Help" for options:
REM   install.bat [-AtStartup] [-MigrateFrom DIR] [-Token TOKEN] [-InstallDir DIR]
REM   install.bat -Restart ^| -Stop ^| -Uninstall [-Purge]
if /i "%~1"=="-Help" (
    powershell.exe -NoProfile -Command "Get-Help '%~dp0install.ps1' -Detailed"
    exit /b 0
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "CODE=%errorlevel%"
REM Keep the window open when started by double-clicking, so the result can be read.
echo %cmdcmdline% | find /i "%~nx0" >nul && pause
exit /b %CODE%
