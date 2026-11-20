@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CS2 Demo 分析器

if not exist ".venv\Scripts\python.exe" (
    echo 首次运行，正在准备运行环境（约需 1-3 分钟）...
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo 正在启动分析器，浏览器将自动打开...
start "" /min cmd /c "timeout /t 3 >nul & start """" http://127.0.0.1:8765"
".venv\Scripts\python.exe" app.py

echo.
echo 服务已停止，按任意键关闭窗口。
pause >nul
