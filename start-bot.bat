@echo off
REM Start script for ComicsBot on Windows.
REM
REM   start-bot.bat            Set up the virtualenv (first run) and start the bot.
REM                            The bot is restarted automatically if it crashes.
REM   start-bot.bat install    Start the bot automatically when you log in to Windows
REM   start-bot.bat uninstall  Remove the automatic start
setlocal EnableExtensions

set "BOT_DIR=%~dp0"
if "%BOT_DIR:~-1%"=="\" set "BOT_DIR=%BOT_DIR:~0,-1%"
set "VENV_DIR=%BOT_DIR%\.venv"
set "TASK_NAME=ComicsBot"

REM The bot reads comics.json and <guild>-Comics.json relative to the working
REM directory in places, so always run it from its own folder.
cd /d "%BOT_DIR%"

if /i "%~1"=="install" goto install
if /i "%~1"=="uninstall" goto uninstall
if not "%~1"=="" (
    echo Usage: %~nx0 [install^|uninstall]
    exit /b 2
)

if not exist ".env" (
    echo Missing %BOT_DIR%\.env - see .env.example for the required settings.
    exit /b 1
)
if not exist "comics.json" (
    echo Missing %BOT_DIR%\comics.json
    exit /b 1
)

call :setup_venv || exit /b 1

:loop
echo [%date% %time%] Starting ComicsBot...
"%VENV_DIR%\Scripts\python.exe" ComicsBot.py
echo [%date% %time%] ComicsBot exited with code %errorlevel%, restarting in 10 seconds (Ctrl+C to stop)...
timeout /t 10 /nobreak >nul
goto loop

:setup_venv
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating virtual environment in %VENV_DIR% ...
    where py >nul 2>&1 && (py -3 -m venv "%VENV_DIR%") || (python -m venv "%VENV_DIR%")
    if not exist "%VENV_DIR%\Scripts\python.exe" (
        echo Could not create virtual environment. Is Python 3 installed and on PATH?
        exit /b 1
    )
)
REM Reinstall dependencies only when requirements.txt has changed.
fc /b "requirements.txt" "%VENV_DIR%\requirements.installed" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies ...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet --upgrade pip
    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet -r requirements.txt || exit /b 1
    copy /y "requirements.txt" "%VENV_DIR%\requirements.installed" >nul
)
exit /b 0

:install
call :setup_venv || exit /b 1
REM Runs at logon of the current user, minimised, without needing admin rights.
schtasks /Create /F /TN "%TASK_NAME%" /SC ONLOGON /RL LIMITED ^
    /TR "cmd /c start \"ComicsBot\" /min \"%~f0\""
if errorlevel 1 exit /b 1
echo Registered scheduled task "%TASK_NAME%". The bot will start when you log in.
echo Start it now with:  schtasks /Run /TN "%TASK_NAME%"
exit /b 0

:uninstall
schtasks /Delete /F /TN "%TASK_NAME%"
exit /b %errorlevel%
