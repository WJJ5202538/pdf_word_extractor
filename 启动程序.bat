@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "src\main.py"
    exit /b 0
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "src\main.py"
    exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -c "import pdfplumber, docx" >nul 2>nul
    if not errorlevel 1 (
        py -3 "src\main.py"
        exit /b %errorlevel%
    )
)

echo.
echo [错误] 未找到可用的运行环境（或缺少 pdfplumber / python-docx）。
echo.
echo 请先在项目目录执行：
echo   py -3.13 -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -e ".[ocr]"
echo.
echo 如果只需要文本层和基础表格，把最后一行换成：
echo   .venv\Scripts\python.exe -m pip install -e .
echo.
pause
exit /b 1
