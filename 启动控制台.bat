@echo off
rem MAA 挂机控制台 - 双击启动 GUI（无控制台窗口）
cd /d "%~dp0"
if exist "%CD%\gui\runtime\pythonw.exe" (
    start "" "%CD%\gui\runtime\pythonw.exe" "%CD%\gui\main.py"
) else (
    start "" "%CD%\gui\.venv\Scripts\pythonw.exe" "%CD%\gui\main.py"
)
