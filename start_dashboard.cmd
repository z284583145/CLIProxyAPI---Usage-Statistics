@echo off
setlocal
cd /d "%~dp0"

set "PY=C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

"%PY%" -u usage_dashboard.py run

echo.
echo Services stopped.
pause
