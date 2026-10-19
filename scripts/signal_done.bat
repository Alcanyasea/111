@echo off
rem MAA task-finished callback: run signal_done.ps1 from this folder.
rem Probe PowerShell 7 explicitly (same reason as the launcher: bare pwsh.exe
rem is not resolved under Task Scheduler / Store execution alias). Bundled
rem portable pwsh in the installer wins, then MSI install, alias, PATH.
set "PWSH=%~dp0..\gui\runtime\pwsh\pwsh.exe"
if not exist "%PWSH%" set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not exist "%PWSH%" set "PWSH=%LocalAppData%\Microsoft\WindowsApps\pwsh.exe"
if not exist "%PWSH%" set "PWSH="
for /f "delims=" %%i in ('where pwsh 2^>nul') do if not defined PWSH set "PWSH=%%i"
if not defined PWSH exit /b 1
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0signal_done.ps1"
