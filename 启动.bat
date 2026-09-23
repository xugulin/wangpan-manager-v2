@echo off
chcp 936 >nul
rem ============================================================================
rem  网盘管理 V2 一键启动（Windows）—— 双击即可
rem ============================================================================
rem  这个文件是 **GBK(cp936)** 编码：中文 Windows 的控制台默认就是 936，
rem  用 UTF-8 存反而会显示成乱码。改它的时候请保持 GBK。
rem
rem  它只做三件事：找到项目自带的解释器 → 切到项目目录 → 运行 启动.py。
rem  V2 自带 运行环境\venv（与 V1、系统 python 都不共享）。
rem ============================================================================
setlocal
set "HERE=%~dp0"
set "PROJ=%HERE%"
if not exist "%PROJ%启动.py" (
  echo [X] 这里不像是 网盘管理 V2 的项目目录：%PROJ%
  echo     应该有 启动.py 与 wangpan\ 目录。
  pause
  exit /b 1
)
set "PY=%PROJ%运行环境\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [!] 找不到项目自带的解释器：%PY%
  echo     V2 自带运行环境，先按 README 建好：
  echo       python -m venv 运行环境\venv
  echo       运行环境\venv\Scripts\python.exe -m pip install PySide6
  pause
  exit /b 1
)
cd /d "%PROJ%"
"%PY%" "%PROJ%启动.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [X] 启动失败（退出码 %RC%）。
  pause
)
exit /b %RC%
