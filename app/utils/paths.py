"""冻结(PyInstaller)与源码运行两种模式下的资源路径解析。"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """应用根目录。

    - 源码运行: universal_scene_forge/
    - onedir 打包: UniversalSceneForge/  (脚本与 settings 同级)
    """
    if getattr(sys, "frozen", False):  # PyInstaller onedir
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def scripts_dir() -> Path:
    """DCC 内部脚本目录（随包分发的 scripts/）。

    PyInstaller 6+ onedir 把 datas 放在 _internal/ 下, 故打包模式优先
    在 _internal/scripts 查找; 兼容手动拷贝到 exe 同级的场景。
    """
    return _bundled_dir("scripts")


def extensions_dir() -> Path:
    """随包分发的 CEP 扩展根目录（Premiere Pro 抽帧面板源）。"""
    return _bundled_dir("extensions")


def _bundled_dir(name: str) -> Path:
    root = resource_root()
    for base in (root / "_internal", root):
        cand = base / name
        if cand.is_dir():
            return cand
    return root / name


def docs_dir() -> Path:
    """随包分发的文档目录（用户手册 / 部署流程图）。"""
    root = resource_root()
    for base in (root / "_internal", root):
        cand = base / "docs"
        if cand.is_dir():
            return cand
    return root / "docs"


def script_path(*parts: str) -> Path:
    return scripts_dir().joinpath(*parts)


def external_pkgs_dir() -> Path:
    """冻结壳运行期 pip 补装目录（依赖体检「一键安装」的 --target）。

    冻结模式固定在用户主目录（安装目录通常位于 Program Files, 无管理员
    权限不可写）; 源码模式用项目根, 便于开发调试。
    """
    if getattr(sys, "frozen", False):
        return Path.home() / ".universal_scene_forge" / "python_pkgs"
    return resource_root() / "python_pkgs"


def register_external_pkgs() -> None:
    """把 python_pkgs 追加到 sys.path 末尾。

    追加而非前插: 不遮蔽随包内置库; 目录尚不存在也无妨（PathFinder 跳过）,
    pip 补装后同一进程内即可 import, 无需重启。
    """
    d = external_pkgs_dir()
    if str(d) not in sys.path:
        sys.path.append(str(d))
