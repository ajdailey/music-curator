@echo off
setlocal enabledelayedexpansion
rem One-time setup: create the virtualenv and install the package.
rem Safe to re-run.
rem
rem   scripts\setup.bat          create the virtualenv and install
rem   scripts\setup.bat --check  report what is missing and change nothing

set "PROJECT_ROOT=%~dp0.."
set "VENV_DIR=%PROJECT_ROOT%\.venv"
set "CHECK_ONLY=0"
if /i "%~1"=="--check" set "CHECK_ONLY=1"
if /i "%~1"=="-h" goto :usage
if /i "%~1"=="--help" goto :usage

echo ==^> Checking Python
rem The py launcher ships with the python.org installer and picks the newest
rem interpreter. Fall back to whatever python is on PATH; the Microsoft Store
rem and most CI images provide that instead.
set "PY=py -3"
%PY% -c "import sys" >nul 2>&1 || set "PY=python"
%PY% -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    echo        Install Python 3.11 or newer from https://www.python.org/downloads/
    echo        and tick "Add python.exe to PATH".
    exit /b 1
)
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo ERROR: Python 3.11 or newer is required.
    %PY% -V
    exit /b 1
)
for /f "delims=" %%v in ('%PY% -V') do echo     %%v

echo ==^> Checking the venv module
%PY% -c "import venv, ensurepip" >nul 2>&1
if errorlevel 1 (
    echo ERROR: this interpreter cannot create virtualenvs.
    echo        Reinstall Python from https://www.python.org/downloads/ with the
    echo        default options; the Windows installer includes venv and pip.
    exit /b 1
)
echo     ok

if "%CHECK_ONLY%"=="1" (
    echo ==^> Checking the virtualenv
    if exist "%VENV_DIR%\Scripts\music-playlists.exe" (
        echo     ok: %VENV_DIR%
    ) else (
        echo     missing: run scripts\setup.bat to create it
    )
    exit /b 0
)

echo ==^> Creating the virtualenv
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo     reusing %VENV_DIR%
) else (
    %PY% -m venv "%VENV_DIR%" || exit /b 1
    echo     created %VENV_DIR%
)

echo ==^> Installing music-curator (editable, with plex and dev extras)
"%VENV_DIR%\Scripts\python.exe" -m pip install --quiet --upgrade pip || exit /b 1
"%VENV_DIR%\Scripts\python.exe" -m pip install --quiet -e "%PROJECT_ROOT%[plex,dev]" || exit /b 1
> "%VENV_DIR%\.install-stamp" echo installed

echo ==^> Verifying
"%VENV_DIR%\Scripts\music-playlists.exe" --help >nul || exit /b 1
"%VENV_DIR%\Scripts\python.exe" -m pytest --version >nul || exit /b 1
echo     music-playlists and pytest are runnable

echo.
echo Setup complete.
echo.
echo Activate the virtualenv with:
echo     .venv\Scripts\activate
echo.
echo Then:
echo     music-playlists validate
echo     pytest
echo     music-playlists generate
echo     music-playlists report
echo.
echo Generation needs two exports in the project root; see README.md:
echo     Library Playlist.m3u8    the catalog
echo     iPod Sync.m3u8           the portable library
echo.
echo For Plex synchronization, copy .env.example to .env and fill in the token.
exit /b 0

:usage
echo Usage: scripts\setup.bat [--check]
exit /b 0
