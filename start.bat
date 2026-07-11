@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title 3GPP Proposal Analyzer

set DIR=%~dp0
set BACKEND=%DIR%proposal-backend.exe
set FRONTEND=%DIR%desktop-dist
set PORT_BACKEND=8765
set PORT_FRONTEND=3000

echo =========================================
echo   3GPP Proposal Analyzer - Portable
echo =========================================
echo.

:: Check files exist
if not exist "%BACKEND%" (
    echo [ERROR] proposal-backend.exe not found
    echo   Place it in: %DIR%
    pause
    exit /b 1
)
if not exist "%FRONTEND%\index.html" (
    echo [ERROR] desktop-dist folder missing or incomplete
    echo   Expected: %FRONTEND%
    pause
    exit /b 1
)

:: Kill any existing backend on the same port
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT_BACKEND%" ^| findstr "LISTENING" 2^>nul') do (
    echo Cleaning up existing process on port %PORT_BACKEND% ^(PID %%a^)...
    taskkill /PID %%a /F >nul 2>&1
)

:: Start backend
echo Starting backend...
start "ProposalBackend" "%BACKEND%"
echo Waiting for backend to be ready...

for /L %%i in (1,1,30) do (
    curl -s http://127.0.0.1:%PORT_BACKEND%/api/health >nul 2>&1
    if !errorlevel! == 0 goto backend_ready
    timeout /t 1 /nobreak >nul
)

echo.
echo [ERROR] Backend failed to start after 30s.
echo   Check if proposal-backend.exe crashed. Try running it manually:
echo   "%BACKEND%"
echo.
pause
exit /b 1

:backend_ready
echo Backend ready ^(port %PORT_BACKEND%^)

:: Start frontend
where python >nul 2>&1
if %errorlevel% == 0 (
    echo Starting frontend with Python...
    start /B "" python -m http.server %PORT_FRONTEND% --bind 127.0.0.1 --directory "%FRONTEND%" >nul 2>&1
) else (
    echo Starting frontend with PowerShell...
    if not exist "%DIR%serve.ps1" (
        echo [ERROR] serve.ps1 not found
        pause
        exit /b 1
    )
    start /B "" powershell -NoProfile -ExecutionPolicy Bypass -File "%DIR%serve.ps1" "%FRONTEND%" %PORT_FRONTEND% >nul 2>&1
)

:: Open browser
echo Opening browser...
start http://localhost:%PORT_FRONTEND%

echo.
echo =========================================
echo   Ready!  http://localhost:%PORT_FRONTEND%
echo   Close this window to stop all services.
echo =========================================

:: Keep alive
:loop
timeout /t 5 /nobreak >nul
goto loop
