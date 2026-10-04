@echo off
REM Start script for ComicsBot on Windows.
REM
REM   start-bot.bat            Set up the virtualenv (first run) and start the bot.
REM                            The bot is restarted automatically if it crashes.
REM   start-bot.bat setup      Only create/update the virtualenv
REM   start-bot.bat install    Start the bot from this folder automatically when you log in
REM                            (add -AtStartup, as administrator, to start it at boot instead)
REM   start-bot.bat uninstall  Remove the automatic start
REM
REM For a clean install from a release archive, use install.bat instead.
setlocal EnableExtensions

set "BOT_DIR=%~dp0"
if "%BOT_DIR:~-1%"=="\" set "BOT_DIR=%BOT_DIR:~0,-1%"
set "VENV_DIR=%BOT_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "TASK_NAME=ComicsBot"
REM Exit codes used by ComicsBot.py
set "EXIT_RESTART=75"
set "EXIT_CONFIG_ERROR=78"

cd /d "%BOT_DIR%"

if /i "%~1"=="install" goto install
if /i "%~1"=="uninstall" goto uninstall
if /i "%~1"=="setup" (
    call :setup_venv
    exit /b
)
if not "%~1"=="" (
    echo Usage: %~nx0 [setup^|install^|uninstall]
    exit /b 2
)

if not exist ".env" (
    echo Missing %BOT_DIR%\.env - copy .env.example to .env and fill in DISCORD_TOKEN.
    exit /b 1
)

call :setup_venv || exit /b 1

:loop
echo [%date% %time%] Starting ComicsBot...
"%VENV_PY%" ComicsBot.py
set "CODE=%errorlevel%"
if "%CODE%"=="0" exit /b 0
if "%CODE%"=="%EXIT_CONFIG_ERROR%" (
    echo Configuration error, not restarting. Fix .env and start again.
    exit /b %CODE%
)
if "%CODE%"=="%EXIT_RESTART%" (
    echo Restarting ComicsBot...
    goto loop
)
echo [%date% %time%] ComicsBot exited with code %CODE%, restarting in 10 seconds (Ctrl+C to stop)...
timeout /t 10 /nobreak >nul
goto loop

:setup_venv
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
    if errorlevel 1 (
        echo Existing virtual environment uses Python older than 3.10, recreating it...
        rmdir /s /q "%VENV_DIR%"
    )
)
if not exist "%VENV_PY%" (
    echo Creating virtual environment in %VENV_DIR% ...
    where py >nul 2>&1 && (py -3 -m venv "%VENV_DIR%") || (python -m venv "%VENV_DIR%")
    if not exist "%VENV_PY%" (
        echo Could not create virtual environment. Is Python 3.10+ installed and on PATH?
        exit /b 1
    )
    "%VENV_PY%" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
    if errorlevel 1 (
        echo ComicsBot needs Python 3.10 or newer. Install it from https://www.python.org/downloads/
        rmdir /s /q "%VENV_DIR%"
        exit /b 1
    )
)
REM Reinstall dependencies only when requirements.txt has changed.
fc /b "requirements.txt" "%VENV_DIR%\requirements.installed" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies ...
    "%VENV_PY%" -m pip install --quiet --upgrade pip
    "%VENV_PY%" -m pip install --quiet -r requirements.txt || exit /b 1
    copy /y "requirements.txt" "%VENV_DIR%\requirements.installed" >nul
)
exit /b 0

:install
REM install.ps1 registers the task without Windows' default 3-day run-time limit.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%BOT_DIR%\install.ps1" -InstallDir "%BOT_DIR%" %2
exit /b %errorlevel%

:uninstall
schtasks /Delete /F /TN "%TASK_NAME%"
exit /b %errorlevel%
