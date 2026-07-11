@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title 3GPP Proposal Analyzer

set DIR=%~dp0
set BACKEND=%DIR%proposal-backend.exe
set VENV_PYTHON=%DIR%venv\Scripts\python.exe
set FRONTEND=%DIR%desktop-dist
set PORT_BACKEND=8765
set PORT_FRONTEND=3000

echo ══════════════════════════════════════
echo   3GPP Proposal Analyzer — Portable
echo ══════════════════════════════════════
echo.

:: Try standalone binary first, fall back to venv
if exist "%BACKEND%" (
    echo Starting backend (standalone)...
    start /B "" "%BACKEND%" >nul 2>&1
    set USE_BINARY=1
) else if exist "%VENV_PYTHON%" (
    echo Starting backend (venv)...
    start /B "" "%VENV_PYTHON%" "%DIR%backend\run.py" >nul 2>&1
    set USE_BINARY=0
) else (
    echo [ERROR] No backend found.
    echo   Place proposal-backend.exe in this folder, OR
    echo   Run: python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

:: Wait for backend
echo Waiting for backend...
for /L %%i in (1,1,30) do (
    curl -s http://127.0.0.1:%PORT_BACKEND%/api/health >nul 2>&1
    if !errorlevel! == 0 goto backend_ready
    timeout /t 1 /nobreak >nul
)
echo [ERROR] Backend failed to start
pause
exit /b 1

:backend_ready
echo Backend ready

:: Start frontend server
echo Starting frontend...
start /B "" python -m http.server %PORT_FRONTEND% --bind 127.0.0.1 --directory "%FRONTEND%" >nul 2>&1

echo.
echo   Open in browser: http://localhost:%PORT_FRONTEND%
echo   Close this window to stop all services.
echo.

:: Keep window open and handle exit
:loop
timeout /t 2 /nobreak >nul
goto loop
