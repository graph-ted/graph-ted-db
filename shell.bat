@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo graph-ted-db: no .venv yet. Double-click setup.bat first.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
title graph-ted-db
echo graphted-db CLI is ready. Try:  graphted-db --help
echo Leave this window with:  exit
echo.
cmd /k
