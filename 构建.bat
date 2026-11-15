@echo off
setlocal
chcp 936 >nul
cd /d "%~dp0"

echo ============================================
echo   影之刃零 存档工具  -  构建单文件 EXE
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 goto nopy

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 goto noinst

echo 清理旧产物...
if exist build rmdir /s /q build
if exist Release rmdir /s /q Release

echo.
echo [1/2] 打包（约 1~2 分钟）...
echo.
python -m PyInstaller ^
  --noconfirm --clean --onefile --noconsole ^
  --name PBZSaveTool ^
  --add-data "%~dp0native;native" ^
  --add-data "%~dp0data;data" ^
  --add-data "%~dp0pbz_key.txt;." ^
  --distpath "%~dp0Release" --workpath "%~dp0build" --specpath "%~dp0build" ^
  "%~dp0gui.py"

if errorlevel 1 goto failed

echo.
echo [2/2] 强制 PE 子系统为 GUI（3），杜绝控制台窗口...
python "%~dp0tools\patch_gui_subsystem.py" "%~dp0Release\PBZSaveTool.exe"
if errorlevel 1 goto failed

echo.
echo ============================================
echo   完成： Release\PBZSaveTool.exe
echo ============================================
if exist "%~dp0Release\PBZSaveTool.exe" for %%F in ("%~dp0Release\PBZSaveTool.exe") do echo   大小 %%~zF 字节
echo.
pause
exit /b 0

:nopy
echo [错误] 找不到 python。请先安装 Python 3.10+ 并加入 PATH。
pause
exit /b 1

:noinst
echo 未检测到 PyInstaller，正在安装...
python -m pip install --upgrade pyinstaller
echo 安装完成后请重新运行本脚本。
pause
exit /b 1

:failed
echo.
echo [失败] 打包出错，请看上面的日志。
pause
exit /b 1