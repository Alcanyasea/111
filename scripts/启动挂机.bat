@echo off
chcp 65001 >nul
title MAA Auto Farm
rem This bat lives in scripts\, project root is one level up
for %%p in ("%~dp0..") do set "PROJ=%%~fp\"
set "PS1=%PROJ%scripts\master.ps1"

rem Fix (same root cause as v2.0.1): bare pwsh.exe alias is not resolved by
rem Task Scheduler / some launch paths (Store-edition PowerShell => 0x80070002,
rem silent no-start). Probe PowerShell 7 explicitly:
rem MSI default location -> Store execution alias -> PATH.
set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not exist "%PWSH%" set "PWSH=%LocalAppData%\Microsoft\WindowsApps\pwsh.exe"
if not exist "%PWSH%" set "PWSH="
for /f "delims=" %%i in ('where pwsh 2^>nul') do if not defined PWSH set "PWSH=%%i"
if not defined PWSH (
    echo ERROR: PowerShell 7 ^(pwsh^) not found.
    echo Please install PowerShell 7: https://aka.ms/powershell
    pause
    exit /b 1
)

echo ========================================
echo   MAA Auto Farm - Starting...
echo   Log: %PROJ%scripts\master_log.txt
echo ========================================
echo.
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -NoShutdown
echo.
echo All done! Press any key to close...
pause >nul
