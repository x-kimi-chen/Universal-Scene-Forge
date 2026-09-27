@echo off
chcp 65001 >nul
REM Universal Scene Forge - Windows build script
REM Output: dist\UniversalSceneForge\  +  installer\UniversalSceneForge_Setup.exe
REM Requires: Python 3.10-3.12 (open3d wheel upper bound), NSIS makensis on PATH (optional)

setlocal enabledelayedexpansion
cd /d "%~dp0\.."

REM ---- interpreter selection: prefer 3.12 (open3d supports 3.8-3.12) ----
set "PYEXE=py -3.12"
%PYEXE% -c "print(1)" >nul 2>&1
if errorlevel 1 set "PYEXE=python"

echo [1/4] 创建虚拟环境 (interpreter: %PYEXE%) ...
if not exist .venv\Scripts\python.exe (
    %PYEXE% -m venv .venv >nul 2>&1
)
if exist .venv\Scripts\python.exe (
    set "PVCMD=.venv\Scripts\python.exe"
) else (
    echo   venv 不可用, 直接使用系统解释器构建。
    set "PVCMD=%PYEXE%"
)

echo [2/4] 安装依赖(失败自动切换清华镜像)...
%PVCMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo 直连 PyPI 失败, 切换清华镜像重试...
    %PVCMD% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple || goto :fail
)

echo [3/4] PyInstaller 打包(onedir)...
%PVCMD% -m pip install pyinstaller || goto :fail
%PVCMD% -m PyInstaller installer\UniversalSceneForge.spec --noconfirm --clean || goto :fail

REM 同步 3DGS 训练仓库源码到 dist(随安装包分发, 训练环境仍隔离)
REM /XD build: 子模块 MSVC 编译中间产物(.pyd 长路径 NSIS 打不开, 且无分发价值)
if exist external\gaussian-splatting\train.py (
    echo    同步 external\gaussian-splatting -^> dist ...
    robocopy external\gaussian-splatting dist\UniversalSceneForge\external\gaussian-splatting /E /XD input .venv build /XF *.pyc /NFL /NDL /NJH /NJS >nul
)

echo [4/4] NSIS 安装向导(未安装 makensis 时跳过)...
where makensis >nul 2>nul
if %errorlevel%==0 (
    set "MAKENSIS=makensis"
) else if exist "C:\Program Files (x86)\NSIS\makensis.exe" (
    set "MAKENSIS=C:\Program Files (x86)\NSIS\makensis.exe"
) else if exist "C:\Program Files\NSIS\makensis.exe" (
    set "MAKENSIS=C:\Program Files\NSIS\makensis.exe"
)
if defined MAKENSIS (
    pushd installer
    "%MAKENSIS%" usf_setup.nsi
    if errorlevel 1 (popd & goto :fail)
    popd
    echo.
    echo 构建完成: installer\UniversalSceneForge_Setup.exe
) else (
    echo 未检测到 makensis, 跳过安装向导。仅生成绿色目录: dist\UniversalSceneForge\
)
goto :end

:fail
echo.
echo *** 构建失败, 请检查上方日志 ***
exit /b 1

:end
endlocal
