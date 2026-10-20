@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo ==========================================================
echo AI Fat Fish - Windows first-time setup using Conda
echo ==========================================================

call "windows\_conda_setup.bat" find
if errorlevel 1 goto :failed

echo [INFO] Checking environment %PET_ENV_NAME% ...
call "%PET_CONDA_BAT%" run -n "%PET_ENV_NAME%" python -V >nul 2>&1
if errorlevel 1 (
    echo [INFO] Creating %PET_ENV_NAME% with Python 3.11 ...
    call "%PET_CONDA_BAT%" create -y -n "%PET_ENV_NAME%" python=3.11
    if errorlevel 1 goto :failed
)

call "windows\_conda_setup.bat" activate
if errorlevel 1 goto :failed

echo [INFO] Installing project dependencies ...
python -m pip install -r requirements.txt
if errorlevel 1 goto :failed

python -c "import PySide6; import pet; from pet.autonomy_intent import parse_action_intent; print('[OK] Runtime and autonomy imports passed')"
if errorlevel 1 goto :failed
echo.
echo [OK] Installation complete. Double-click 2-Run-Windows.bat.
pause
exit /b 0

:failed
echo.
echo [ERROR] Setup failed. Review the messages above before retrying.
pause
exit /b 1
