@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo ==========================================================
echo AI Fat Fish - Windows test runtime
echo ==========================================================
call "windows\_conda_setup.bat" activate
if errorlevel 1 goto :failed

python -c "import PySide6; import pet.autonomy"
if errorlevel 1 (
    echo [ERROR] Missing dependencies. Run 1-Install-Windows.bat first.
    goto :failed
)

echo [INFO] Starting desktop pet. Leave this window open to see errors.
echo [INFO] To enable AI: Settings - AI and Chat - AI Autonomous Behavior.
echo [INFO] The LLM API must be configured and enabled by you.
python -m pet
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo [ERROR] Could not start the AI pet.
pause
exit /b 1
