@echo off
rem Resolve the sibling script and PowerShell without depending on the caller's cwd.
setlocal
set "PWSH=%~dp0..\gui\runtime\pwsh\pwsh.exe"
if not exist "%PWSH%" set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not exist "%PWSH%" set "PWSH=%LocalAppData%\Microsoft\WindowsApps\pwsh.exe"
if not exist "%PWSH%" set "PWSH="
for /f "delims=" %%i in ('where pwsh 2^>nul') do if not defined PWSH set "PWSH=%%i"
if not defined PWSH exit /b 1
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dpn0.ps1"
exit /b %errorlevel%
