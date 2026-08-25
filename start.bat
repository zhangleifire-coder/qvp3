@echo off
rem 图文生产平台 一键启动（双击运行；stop/status 可拖到本文件后加参数不可用，请用 Git Bash）
cd /d "%~dp0"
where bash >nul 2>nul
if %errorlevel%==0 (
  bash scripts/start-all.sh %1
) else (
  if exist "C:\Program Files\Git\bin\bash.exe" (
    "C:\Program Files\Git\bin\bash.exe" scripts/start-all.sh %1
  ) else (
    echo 未找到 Git Bash，请安装 Git for Windows 或用 Git Bash 手动执行 scripts/start-all.sh
  )
)
pause
