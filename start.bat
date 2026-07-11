@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion
title 3GPP Proposal Analyzer

set "DIR=%~dp0"
set "BACKEND=!DIR!proposal-backend.exe"
set "FRONTEND=!DIR!desktop-dist"
set "SERVE_SCRIPT=!DIR!serve.ps1"
set "PORT_BACKEND=8765"
set "PORT_FRONTEND=3000"
set "LOG_DIR=!LOCALAPPDATA!\3GPP Proposal Analyzer\logs"
set "BACKEND_LOG=!LOG_DIR!\portable-backend.log"
set "FRONTEND_LOG=!LOG_DIR!\portable-frontend.log"
set "PROPOSAL_TOOL_PORT=!PORT_BACKEND!"
set "PROPOSAL_TOOL_LOG_FILE=!BACKEND_LOG!"

if not exist "!LOG_DIR!" mkdir "!LOG_DIR!" >nul 2>&1

echo =========================================
echo   3GPP Proposal Analyzer - Portable
echo =========================================
echo.

:: Check files exist
if not exist "!BACKEND!" (
    echo [ERROR] proposal-backend.exe not found
    echo   Place it in: "!DIR!"
    pause
    exit /b 1
)
if not exist "!FRONTEND!\index.html" (
    echo [ERROR] desktop-dist folder missing or incomplete
    echo   Expected: "!FRONTEND!"
    pause
    exit /b 1
)
if not exist "!SERVE_SCRIPT!" (
    echo [ERROR] serve.ps1 not found
    echo   Expected: "!SERVE_SCRIPT!"
    pause
    exit /b 1
)

:: Kill any existing backend on the same port
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":!PORT_BACKEND!" ^| findstr "LISTENING" 2^>nul') do (
    echo Cleaning up existing process on port !PORT_BACKEND! ^(PID %%a^)...
    taskkill /PID %%a /F >nul 2>&1
)

:: Start backend
echo Starting backend...
start "" /B "!BACKEND!" >>"!BACKEND_LOG!" 2>&1
echo Waiting for backend to be ready...

for /L %%i in (1,1,30) do (
    curl.exe --fail --silent --show-error "http://127.0.0.1:!PORT_BACKEND!/api/health" >nul 2>&1
    if not errorlevel 1 goto backend_ready
    timeout /t 1 /nobreak >nul
)

echo.
echo [ERROR] Backend failed to start after 30s.
echo   Backend log: "!BACKEND_LOG!"
if exist "!BACKEND_LOG!" (
    echo.
    echo ---------- backend log ----------
    type "!BACKEND_LOG!"
    echo -------- end backend log --------
)
echo.
pause
exit /b 1

:backend_ready
echo Backend ready ^(port !PORT_BACKEND!^)

:: Start frontend with Windows PowerShell (available on Windows 10/11)
echo Starting frontend with PowerShell...
start "" /B powershell.exe -NoProfile -ExecutionPolicy Bypass -File "!SERVE_SCRIPT!" "!FRONTEND!" !PORT_FRONTEND! >>"!FRONTEND_LOG!" 2>&1

echo Waiting for frontend to be ready...
for /L %%i in (1,1,15) do (
    curl.exe --fail --silent --show-error "http://127.0.0.1:!PORT_FRONTEND!/" >nul 2>&1
    if not errorlevel 1 goto frontend_ready
    timeout /t 1 /nobreak >nul
)

echo.
echo [ERROR] Frontend failed to start after 15s.
echo   Frontend log: "!FRONTEND_LOG!"
if exist "!FRONTEND_LOG!" type "!FRONTEND_LOG!"
pause
exit /b 1

:frontend_ready
echo Opening browser...
start "" "http://127.0.0.1:!PORT_FRONTEND!/"

echo.
echo =========================================
echo   Ready!  http://127.0.0.1:!PORT_FRONTEND!/
echo   Close this window to stop all services.
echo =========================================

:: Keep alive
:loop
timeout /t 5 /nobreak >nul
goto loop
