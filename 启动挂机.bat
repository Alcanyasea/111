@echo off
chcp 65001 >nul
title MAA Auto Farm - 3 Accounts
echo ========================================
echo   MAA Auto Farm - Starting...
echo   Log: D:\1\scripts\master_log.txt
echo ========================================
echo.
powershell.exe -ExecutionPolicy Bypass -File "D:\1\scripts\master.ps1"
echo.
echo All done! Press any key to close...
pause >nul
