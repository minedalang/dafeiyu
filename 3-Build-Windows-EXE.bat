@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo ==========================================================
echo AI Fat Fish - build a native Windows executable locally
echo ==========================================================
call "windows\_conda_setup.bat" activate
if errorlevel 1 goto :failed

python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller ...
    python -m pip install "pyinstaller>=6"
    if errorlevel 1 goto :failed
)

echo [INFO] Building webm-chat variant (AI feature included) ...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\build_onedir.ps1" -Variant webm-chat
if errorlevel 1 goto :failed

echo.
echo [OK] Build completed. Check dist-onedir for the portable zip.
pause
exit /b 0

:failed
echo.
echo [ERROR] Windows build failed. Check the log above.
pause
exit /b 1
