@echo off
rem Run epubzh from this skill directory without installing anything.
rem usage: epubzh-run.cmd <command> [options]
setlocal
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"
if defined PYTHONPATH (
  set "PYTHONPATH=%DIR%;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%DIR%"
)
set "PY=python"
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
  set "PY=py -3"
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if errorlevel 1 (
    echo epubzh needs Python 3.11 or newer: https://www.python.org/downloads/ 1>&2
    exit /b 1
  )
)
%PY% -m epubzh %*
exit /b %ERRORLEVEL%
