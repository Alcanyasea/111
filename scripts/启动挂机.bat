@echo off
chcp 65001 >nul
echo ========================================
echo   MAA Auto Farm - Starting...
echo   Log: D:\1\scripts\master_log.txt
echo ========================================
echo.
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "D:\1\scripts\master.ps1" -NoShutdown
echo.
echo All done! Press any key to close...
pause >nul
