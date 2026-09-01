@echo off
chcp 65001 >nul
title MAA Farm Console Setup
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
