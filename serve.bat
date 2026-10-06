@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\graphted-db.exe" (
  echo graph-ted-db: no .venv yet. Double-click setup.bat first.
  pause
  exit /b 1
)

if "%~1"=="" (
  set "GRAPH=%CD%\my-graph"
  if not exist "%GRAPH%\graph.json" (
    echo Creating graph folder: %GRAPH%
    ".venv\Scripts\graphted-db.exe" init "%GRAPH%" --name demo --exist-ok
    if errorlevel 1 (
      pause
      exit /b 1
    )
  )
) else (
  set "GRAPH=%~f1"
)

if not exist "%GRAPH%\graph.json" (
  echo graph-ted-db: no graph.json in %GRAPH%
  echo Init it first, e.g.:  graphted-db init "%GRAPH%" --name test
  echo Or pass the same folder you use with ls-nodes / put-node.
  pause
  exit /b 1
)

echo Serving %GRAPH%  -^>  http://127.0.0.1:8099
echo Stop with Ctrl+C. In another window try:
echo   curl -s http://127.0.0.1:8099/health
echo.
".venv\Scripts\graphted-db.exe" serve "%GRAPH%"
echo.
pause
