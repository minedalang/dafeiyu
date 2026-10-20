@echo off
rem Shared Conda locator for the Windows AI-pet test distribution.
rem Keeps the project's Python isolated from an unrelated system Python.

if not defined PET_ENV_NAME set "PET_ENV_NAME=dafeiyu-ai"
if defined DAFEIYU_CONDA_ENV set "PET_ENV_NAME=%DAFEIYU_CONDA_ENV%"

if /I "%~1"=="find" goto :find
if /I "%~1"=="activate" goto :activate
echo Usage: call windows\_conda_setup.bat find^|activate
exit /b 2

:find
if defined PET_CONDA_BAT if exist "%PET_CONDA_BAT%" exit /b 0
if defined DAFEIYU_CONDA_BAT if exist "%DAFEIYU_CONDA_BAT%" (
    set "PET_CONDA_BAT=%DAFEIYU_CONDA_BAT%"
    exit /b 0
)
if defined CONDA_EXE (
    for %%I in ("%CONDA_EXE%") do if exist "%%~dpI..\condabin\conda.bat" (
        set "PET_CONDA_BAT=%%~dpI..\condabin\conda.bat"
        exit /b 0
    )
)
if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" (
    set "PET_CONDA_BAT=%USERPROFILE%\miniconda3\condabin\conda.bat"
    exit /b 0
)
if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" (
    set "PET_CONDA_BAT=%USERPROFILE%\anaconda3\condabin\conda.bat"
    exit /b 0
)
if exist "%ProgramData%\anaconda3\condabin\conda.bat" (
    set "PET_CONDA_BAT=%ProgramData%\anaconda3\condabin\conda.bat"
    exit /b 0
)
for /f "delims=" %%I in ('where conda.bat 2^>nul') do (
    set "PET_CONDA_BAT=%%I"
    exit /b 0
)
echo [ERROR] Conda was not found.
echo Set DAFEIYU_CONDA_BAT to your condabin\conda.bat path and retry.
exit /b 1

:activate
call "%~f0" find
if errorlevel 1 exit /b 1
call "%PET_CONDA_BAT%" activate "%PET_ENV_NAME%"
if errorlevel 1 (
    echo [ERROR] Could not activate Conda environment "%PET_ENV_NAME%".
    echo Run 1-Install-Windows.bat first.
    exit /b 1
)
echo [OK] Conda environment: %PET_ENV_NAME%
python -c "import sys; print('[PYTHON]', sys.executable)"
if errorlevel 1 exit /b 1
exit /b 0
