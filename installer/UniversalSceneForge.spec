# -*- mode: python ; coding: utf-8 -*-
# Universal Scene Forge — PyInstaller 打包规格
# 构建: pyinstaller installer/UniversalSceneForge.spec --noconfirm
# 产物: dist/UniversalSceneForge/ (onedir, 启动快; 配合 NSIS 出安装向导)

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # 项目根 universal_scene_forge/
SCRIPTS = ROOT / "scripts"

block_cipher = None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # DCC 内部脚本随包分发（运行期由 app/utils/paths.py 定位）
        (str(SCRIPTS / "blender"), "scripts/blender"),
        (str(SCRIPTS / "max"), "scripts/max"),
        (str(SCRIPTS / "houdini"), "scripts/houdini"),
        (str(SCRIPTS / "c4d"), "scripts/c4d"),
        (str(SCRIPTS / "metashape"), "scripts/metashape"),
        (str(SCRIPTS / "ue5"), "scripts/ue5"),
        (str(ROOT / "docs"), "docs"),
        # Premiere Pro 抽帧 CEP 扩展源（运行期安装到 %APPDATA%/Adobe/CEP/extensions）
        (str(ROOT / "extensions"), "extensions"),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "plyfile",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 体积优化 (<100MB 安装包): open3d 145MB + dash 34MB + plotly 12MB
        # 不随 GUI 壳打包, 由「依赖体检」引导 pip 补装(deps.py 含 o3d 项)
        "open3d", "dash", "plotly",
        # 训练环境(torch 等)在子进程隔离, 不进 GUI 壳, 控制 exe 体积
        "torch", "torchvision", "tkinter", "matplotlib",
        "IPython", "jedi", "rembg",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UniversalSceneForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                       # GUI 程序不弹黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "installer" / "usf.ico"),
    version=str(ROOT / "installer" / "version_info.txt"),   # Windows 资源版本(D-02)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="UniversalSceneForge",
)

# 说明:
# 1. 体积优化(v0.9.1): open3d/dash/plotly/cv2-完整版 已移出 GUI 壳,
#    削减约 250MB 未压缩体积 → 安装包 <100MB。安装后由「依赖体检」
#    一键 pip 补装 open3d(网格重建核心); cv2 已换 headless 版随包。
# 2. --onefile 模式只需将 exe 段的 exclude_binaries 改为 False 并
#    删除 COLLECT 段, 但启动解包慢, 不推荐。
