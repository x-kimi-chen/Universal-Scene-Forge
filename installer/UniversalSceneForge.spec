# -*- mode: python ; coding: utf-8 -*-
# Universal Scene Forge — PyInstaller 打包规格
# 构建: pyinstaller installer/UniversalSceneForge.spec --noconfirm
# 产物: dist/UniversalSceneForge/ (onedir, 启动快; 配合 NSIS 出安装向导)

import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # 项目根 universal_scene_forge/
SCRIPTS = ROOT / "scripts"

# Conda 版 PySide6 把 Qt6*.dll / shiboken6*.dll 放在 <base>\Library\bin,
# 而不是 site-packages\PySide6。PyInstaller 的依赖扫描依赖 PATH, 这里把
# conda 的 Library\bin 前置, 让 Qt/Shiboken 的依赖被完整收集。
# 该路径仅影响构建机, 不写入产物配置。
_CONDA_LIB = Path(sys.base_prefix) / "Library" / "bin"
if _CONDA_LIB.is_dir():
    os.environ["PATH"] = str(_CONDA_LIB) + os.pathsep + os.environ.get("PATH", "")

# 构建期兼容: PyInstaller 6.22.3 在「conda base + 普通 venv」组合下会把
# numpy 识别为 conda 包, 但 hook-numpy 引用的 conda_support 仅在 pure conda
# 环境定义, 导致 ImportError。这里只把 numpy 的安装器判定改为 pip, 让
# hook 走非 conda 分支; 不修改 numpy 本身, 也不影响产物运行时行为。
try:
    from PyInstaller.utils import hooks as _pyi_hooks
    if _pyi_hooks.get_installer("numpy") == "conda":
        _orig_get_installer = _pyi_hooks.get_installer
        _pyi_hooks.get_installer = (
            lambda name: "pip" if name == "numpy"
            else _orig_get_installer(name))
except Exception:  # noqa: BLE001 —— 构建工具差异不应阻断 spec 解析
    pass

block_cipher = None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # DCC 内部脚本随包分发（运行期由 app/utils/paths.py 定位）
        (str(SCRIPTS / "blender"), "scripts/blender"),
        (str(SCRIPTS / "gaussian"), "scripts/gaussian"),
        (str(SCRIPTS / "max"), "scripts/max"),
        (str(SCRIPTS / "houdini"), "scripts/houdini"),
        (str(SCRIPTS / "c4d"), "scripts/c4d"),
        (str(SCRIPTS / "metashape"), "scripts/metashape"),
        (str(SCRIPTS / "ue5"), "scripts/ue5"),
        (str(ROOT / "docs"), "docs"),
        # Premiere Pro 抽帧 CEP 扩展源（运行期安装到 %APPDATA%/Adobe/CEP/extensions）
        (str(ROOT / "extensions"), "extensions"),
        # 开源合规: 许可证/第三方清单随绿色目录一起分发（NSIS 另有一份）
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD_PARTY_LICENSES.md"), "."),
        (str(ROOT / "NOTICE"), "."),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        # PySide6 的二进制扩展依赖 shiboken6 包；只收集 .pyd 会缺 __init__.py，
        # 启动时报 "Unable to import Shiboken"。显式声明包与扩展模块。
        "shiboken6",
        "shiboken6.Shiboken",
        # 函数内延迟导入的网格后端; 显式声明避免冻结后缺模块
        "app.core.tsdf",
        "app.core.phototexture",
        # numpy 2.x 把 core 降级为兼容别名，PyInstaller 不会自动收集；
        # open3d/cv2 仍会 import numpy.core.multiarray，缺失会导致贴色失败。
        "numpy.core", "numpy.core.multiarray", "numpy.core.umath",
        "numpy._core", "numpy._core.multiarray", "numpy._core.umath",
        # open3d 由「依赖体检」运行期补装到 python_pkgs, PyInstaller 静态
        # 分析看不到它的 import。以下为 open3d 启动链用到的 stdlib 模块,
        # 显式收集避免冻结后出现 "No module named 'uuid'/'zoneinfo'".
        "uuid", "zoneinfo", "email", "http", "xml", "sqlite3", "ssl",
        "bz2", "lzma", "decimal", "pydoc", "pydoc_data", "pdb", "bdb",
        "cProfile", "profile", "pstats", "socketserver", "quopri",
        "mimetypes", "calendar", "secrets", "gettext", "html", "pyexpat",
        "ipaddress", "filecmp", "fnmatch", "colorsys", "timeit",
        # 合规硬约束: 不得加入 plyfile（GPL-3.0+）。PLY 读写走
        # app/utils/ply_io.py（MIT）；external/ 训练环境不属于本产物。
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
        # 合规硬约束: GPL-3.0+ 的 plyfile 绝不进入本产物; 若某依赖隐式
        # 引入它, PyInstaller 会在此明确排除而不是静默打包。
        "plyfile", "plyfile.plyfile", "plyfile.plytypes",
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
# 3. 合规硬约束(LICENSE_COMPLIANCE_CHANGES.md §3):
#    - 不打包 plyfile(GPL-3.0+), 已从 hiddenimports 删除并加入 excludes;
#    - 不打包 external/gaussian-splatting(Inria 非商业), 本 spec 不引用它;
#    - 不打包 tools/ 下的 COLMAP/FFmpeg 二进制, 由用户自备或运行期下载;
#    - Blender/PySide6 保持动态/子进程边界, 不静态链接。
#    build_windows.bat 另有构建后断言, 若 dist 中出现 external/ 直接失败。
