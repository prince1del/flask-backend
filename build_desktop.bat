@echo off
REM Build a Windows desktop executable for Centralized DB System.

python -m pip install -r requirements.txt
pyinstaller --clean --noconfirm desktop_app.spec

if exist "dist\Centralized DB System\Centralized DB System.exe" (
    echo Build complete.
    echo Executable output at "dist\Centralized DB System\Centralized DB System.exe"
) else (
    echo Build failed or executable not found.
)

set "INNOSETUP_PATH="
where.exe iscc.exe >nul 2>&1
if %errorlevel%==0 (
    for /f "usebackq delims=" %%I in (`where.exe iscc.exe`) do set "INNOSETUP_PATH=%%I"
)
if not defined INNOSETUP_PATH if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "INNOSETUP_PATH=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined INNOSETUP_PATH if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "INNOSETUP_PATH=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined INNOSETUP_PATH if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "INNOSETUP_PATH=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if defined INNOSETUP_PATH (
    echo Building installer with Inno Setup...
    "%INNOSETUP_PATH%" installer.iss
) else (
    echo Inno Setup compiler not detected; installer step skipped.
)
pause
