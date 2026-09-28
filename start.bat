@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set PY=
where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py
if not defined PY if defined MIMO_PYTHON set PY=%MIMO_PYTHON%

if not defined PY (
  echo [错误] 未找到 Python。请安装 Python 3.10+ 后重试。
  pause
  exit /b 1
)

echo ==========================================
echo   私人数据时光轴
echo   默认地址: http://127.0.0.1:8764
echo ==========================================

if "%~1"=="" (
  "%PY%" server.py
) else (
  "%PY%" server.py %*
)
