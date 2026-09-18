@echo off
rem Starts the containerized pipeline from cmd, PowerShell, or a double-click.
rem
rem A launcher only. compose.yaml holds the configuration and the scripts inside
rem the image do the work, so there is no Windows path to keep in step. WSL takes
rem the Linux path, scripts/container.sh.
rem
rem   container.bat                   validate, ruff, test, generate, report
rem   container.bat generate          write playlists into output\
rem   container.bat report            the audit report
rem   container.bat publish           copy output\ and reports\ to dist\
rem   container.bat publish D:\cards  publish somewhere else, as --out D:\cards
rem   container.bat shell             a shell in the image
rem   container.bat --rebuild         rebuild the image first

setlocal enabledelayedexpansion

rem Read before the first shift, which moves %0.
set "ROOT="
for %%I in ("%~dp0..") do set "ROOT=%%~fI"

rem A double-clicked window closes on exit and takes the output with it.
set "PAUSE_ON_EXIT="
echo %cmdcmdline% | find /i "%~nx0" >nul && set "PAUSE_ON_EXIT=1"

set "IMAGE=music-curator:local"
if defined MUSIC_CURATOR_IMAGE set "IMAGE=%MUSIC_CURATOR_IMAGE%"
set "MUSIC_CURATOR_IMAGE=%IMAGE%"
set "OUT_DIR=%MUSIC_CURATOR_OUT%"
set "REBUILD="
set "CMD="
set "INTERACTIVE="
set "PUBLISH="

:parse
if "%~1"=="" goto ready
if /i "%~1"=="--rebuild" (set "REBUILD=1" & shift & goto parse)
if /i "%~1"=="--out" goto opt_out
if /i "%~1"=="-h" goto usage
if /i "%~1"=="--help" goto usage

rem The first remaining argument names the command; anything after it is passed
rem through with its quotes, so "Classic Rock" stays one argument. An empty
rem command leaves compose.yaml's own to run.
set CMD=%1
for %%S in (validate lint test generate report clean) do if /i "%~1"=="%%S" set "CMD=scripts/%%S.sh"
if /i "%~1"=="check" (set "CMD=" & goto ready)
if /i "%~1"=="shell" (set "CMD=bash" & set "INTERACTIVE=1")
if /i "%~1"=="publish" goto publish
shift

:rest
if "%~1"=="" goto ready
set CMD=!CMD! %1
shift
goto rest

:opt_out
if "%~2"=="" (echo --out needs a directory& goto fail)
set "OUT_DIR=%~2"
shift & shift & goto parse

rem DIR becomes the /out mount, as --out does. Passed through, it would name a
rem directory inside the container, which is discarded on exit.
:publish
if not "%~3"=="" (echo publish takes at most one directory& goto fail)
if not "%~2"=="" set "OUT_DIR=%~2"
set "CMD=scripts/publish.sh"
set "PUBLISH=1"

:ready
rem Resolved before the cd, so a relative directory is relative to the caller.
set "OUT_ABS=%ROOT%\dist"
if defined OUT_DIR for %%I in ("!OUT_DIR!") do set "OUT_ABS=%%~fI"
set "MUSIC_CURATOR_OUT=!OUT_ABS!"
cd /d "%ROOT%"

rem publish.sh removes output\ and reports\ under its destination before
rem copying, so the project root or a directory inside either is refused.
if not defined PUBLISH goto engine
set "T=!OUT_ABS!\"
if /i "!T!"=="%ROOT%\" goto refuse
if /i not "!T:%ROOT%\output\=!"=="!T!" goto refuse
if /i not "!T:%ROOT%\reports\=!"=="!T!" goto refuse

:engine
set "ENGINE=%MUSIC_CURATOR_CONTAINER_ENGINE%"
if not defined ENGINE where docker >nul 2>&1 && set "ENGINE=docker"
if not defined ENGINE where podman >nul 2>&1 && set "ENGINE=podman"
if not defined ENGINE (
  echo Docker or Podman is required. Install Docker Desktop, or run in WSL with scripts/container.sh.
  goto fail
)
%ENGINE% compose version >nul 2>&1
if errorlevel 1 (
  echo %ENGINE% has no compose support; install the compose plugin.
  goto fail
)

rem A link resolves inside the container, where a target outside the project
rem directory does not exist.
for %%F in ("Library Playlist.m3u8" "iPod Sync.m3u8") do (
  set "ATTR=%%~aF"
  if defined ATTR if not "!ATTR:l=!"=="!ATTR!" echo ==^> Note: '%%~F' is a link; the container sees its target only inside the project directory. Copy the file in.
)

rem A mount source has to exist before the engine is asked to mount it.
if not exist "!OUT_ABS!\" md "!OUT_ABS!"

if defined REBUILD goto build
%ENGINE% image inspect %IMAGE% >nul 2>&1
if errorlevel 1 goto build
goto run

:build
echo ==^> Building %IMAGE% ^(first run pulls the interpreter; later ones are cached^)
%ENGINE% compose build curator
if errorlevel 1 goto fail

:run
rem Without a terminal on both ends, compose refuses to allocate one. cmd cannot
rem test for a console; PowerShell can.
set "TTY_FLAG="
if defined INTERACTIVE goto launch
powershell -NoProfile -NonInteractive -Command "exit [int]([Console]::IsInputRedirected -or [Console]::IsOutputRedirected)" 2>nul
if errorlevel 1 set "TTY_FLAG=-T"

:launch
if defined PUBLISH echo ==^> Publishing to: !OUT_ABS!
%ENGINE% compose run --rm %TTY_FLAG% curator !CMD!
if errorlevel 1 goto fail
echo ==^> Playlists in output\, reports in reports\
if defined PAUSE_ON_EXIT pause
endlocal
exit /b 0

:refuse
echo ERROR: refusing to publish to '!OUT_ABS!': it is the project root, or inside output\ or reports\
goto fail

:usage
echo usage: container.bat [--rebuild] [--out DIR] [command]
echo.
echo   (no command)   check: validate, ruff, pytest, generate, report
echo   validate       knowledge-file validation, no catalog needed
echo   lint           ruff; --fix applies the fixable findings
echo   test           the test suite
echo   generate       write playlists into output\
echo   report         write and print the audit report
echo   publish [DIR]  copy output\ and reports\ to DIR, else the --out directory
echo   clean          remove generated output and reports
echo   shell          an interactive shell in the image
echo   ^<anything^>     run verbatim inside the image, at the project root
echo.
echo   --rebuild      rebuild the image first
echo   --out DIR      the publish destination on this host (default: dist\)
echo.
echo environment:
echo   MUSIC_CURATOR_IMAGE             image name (default music-curator:local)
echo   MUSIC_CURATOR_CONTAINER_ENGINE  docker or podman (default: whichever is present)
echo   MUSIC_CURATOR_OUT               default publish directory, as --out
echo.
echo Generated playlists land in output\ and reports\ in the working tree, which is
echo bind-mounted, so they are on this host whether or not publish is used.
if defined PAUSE_ON_EXIT pause
endlocal
exit /b 0

:fail
if defined PAUSE_ON_EXIT pause
endlocal
exit /b 1
