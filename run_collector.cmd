@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
"C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" usage_dashboard.py collect >> "logs\collector.out.log" 2>> "logs\collector.err.log"
