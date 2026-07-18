@echo off
title NEXORA Server
cd /d "E:\centralized-db-system"

set "PY=E:\centralized-db-system\.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:5000"

if not exist "%PY%" (
  echo ERROR: Python venv not found at:
  echo   %PY%
  echo.
  pause
  exit /b 1
)

REM If port 5000 already listening, just open the browser
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL%==0 (
  echo NEXORA already running on port 5000.
  start "" "%URL%"
  exit /b 0
)

echo Starting NEXORA server...
start "NEXORA Server" /min "%PY%" "E:\centralized-db-system\_run_server_5000.py"

REM Wait until the server is ready (max ~20s)
set /a tries=0
:wait_loop
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if %ERRORLEVEL%==0 goto open_browser
set /a tries+=1
if %tries% LSS 20 goto wait_loop

echo.
echo Server did not start in time. Check the "NEXORA Server" window.
pause
exit /b 1

:open_browser
echo Server is ready. Opening app...
start "" "%URL%"
exit /b 0
