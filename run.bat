@echo off
chcp 65001 >nul
cd /d "%~dp0"

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw "%~dp0main.py"
    exit /b 0
)

where pyw >nul 2>nul
if not errorlevel 1 (
    start "" pyw -3 "%~dp0main.py"
    exit /b 0
)

python "%~dp0main.py"
if errorlevel 1 pause
