@echo off
setlocal
set "PROJECT_DIR=%~dp0"

if exist "%PROJECT_DIR%.venv\Scripts\pythonw.exe" (
    start "" /b "%PROJECT_DIR%.venv\Scripts\pythonw.exe" "%PROJECT_DIR%deepseek_monitor.py"
    if errorlevel 1 exit /b 1
    exit /b 0
)

where pyw >nul 2>nul
if not errorlevel 1 (
    start "" /b pyw -3 "%PROJECT_DIR%deepseek_monitor.py"
    if errorlevel 1 exit /b 1
    exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" /b pythonw "%PROJECT_DIR%deepseek_monitor.py"
    if errorlevel 1 exit /b 1
    exit /b 0
)

where py >nul 2>nul
if not errorlevel 1 (
    start "" py -3 "%PROJECT_DIR%deepseek_monitor.py"
    if errorlevel 1 exit /b 1
    exit /b 0
)

echo Python 3.10 or newer was not found. 1>&2
exit /b 1
