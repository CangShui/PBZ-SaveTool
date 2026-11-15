@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 goto con
start "" pythonw "gui.py"
exit /b 0
:con
python "gui.py"