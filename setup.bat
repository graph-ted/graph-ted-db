@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Finding Python 3.10+ ...
set "PY="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo graph-ted-db: need Python 3.10 or newer.
  echo Install it from https://www.python.org/downloads/
  echo During setup, tick "Add python.exe to PATH".
  pause
  exit /b 1
)

echo Creating .venv ...
%PY% -m venv .venv
if errorlevel 1 (
  echo Failed to create .venv
  pause
  exit /b 1
)

echo Installing graph-ted-db ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 goto :fail

echo.
echo Setup finished.
echo   Start the database:  serve.bat
echo   Open a CLI prompt:   shell.bat
echo.
pause
exit /b 0

:fail
echo Install failed.
pause
exit /b 1
