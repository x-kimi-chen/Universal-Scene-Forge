"""Premiere Pro 抽帧桥接器（输入侧工具, 非导出 DCC）。

职责:
- 探测 Premiere Pro 安装（settings 覆盖 → 注册表 → 常见路径）
- 安装 CEP 扩展 pr_frame_exporter 到 %APPDATA%/Adobe/CEP/extensions,
  并写入 PlayerDebugMode 注册表开关（未签名扩展的加载前提）
- 任务/状态文件读写: 与已安装扩展同目录 —— 面板侧通过
  getSystemPath("extension") 自定位, 双方无需约定用户主目录
- 启动 Premiere（分离进程, 不阻塞 GUI）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, List, Optional

from app.utils.paths import extensions_dir
from app.utils.logger import get_logger

try:
    import winreg
    HAS_WINREG = True
except ImportError:  # 非 Windows（开发环境跑单测）
    HAS_WINREG = False

log = get_logger("PREMIERE")

EXT_BUNDLE_ID = "com.universalsceneforge.prfexport"
PR_EXE_NAME = "Adobe Premiere Pro.exe"
TASK_FILE = "usf_task.json"
STATUS_FILE = "usf_status.json"
# CEP 8~12 对应 Premiere 2018~2025+; PlayerDebugMode 写全, 宿主按自身版本取
CSXS_VERSIONS = (8, 9, 10, 11, 12)

_RegistrySet = Callable[[str, str, str], None]


# ------------------------------------------------------------------ #
#  探测
# ------------------------------------------------------------------ #
def _registry_premiere_dir() -> Optional[str]:
    """HKLM\\SOFTWARE\\Adobe\\Premiere Pro\\CurrentVersion → 安装目录。"""
    if not HAS_WINREG:
        return None
    try:
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Adobe\Premiere Pro\CurrentVersion") as key:
            for name in ("InstallPath", "ApplicationPath"):
                try:
                    val, _t = winreg.QueryValueEx(key, name)
                    if val:
                        return str(val)
                except OSError:
                    continue
    except OSError:
        pass
    return None


def detect_premiere(
        override: Optional[str] = None,
        _reg: Optional[Callable[[], Optional[str]]] = None,
) -> Optional[str]:
    """三级探测 Premiere Pro 主程序: settings 覆盖 → 注册表 → 常见路径。"""
    # 1) 显式路径（settings.premiere_exe, 可为 exe 或其所在目录）
    if override:
        p = Path(override)
        if p.is_file():
            return str(p)
        cand = p / PR_EXE_NAME
        if cand.is_file():
            return str(cand)
    # 2) 注册表
    reg = _reg or _registry_premiere_dir
    d = reg()
    if d:
        p = Path(d)
        if p.is_file() and p.name.lower() == PR_EXE_NAME.lower():
            return str(p)
        cand = p / PR_EXE_NAME
        if cand.is_file():
            return str(cand)
    # 3) 常见路径
    for base in (r"C:\Program Files\Adobe", r"C:\Program Files (x86)\Adobe"):
        root = Path(base)
        if not root.is_dir():
            continue
        for sub in sorted(root.glob("Adobe Premiere Pro*"), reverse=True):
            cand = sub / PR_EXE_NAME
            if cand.is_file():
                return str(cand)
    return None


# ------------------------------------------------------------------ #
#  CEP 扩展安装
# ------------------------------------------------------------------ #
def bundled_extension_dir() -> Path:
    """随包分发的扩展源目录（PyInstaller datas 的 extensions/）。"""
    return extensions_dir() / "pr_frame_exporter"


def cep_extensions_root(appdata: Optional[Path] = None) -> Path:
    base = Path(appdata) if appdata else Path(os.environ.get("APPDATA", ""))
    return base / "Adobe" / "CEP" / "extensions"


def extension_install_dir(appdata: Optional[Path] = None) -> Path:
    return cep_extensions_root(appdata) / EXT_BUNDLE_ID


def _enable_player_debug_mode(_setter: Optional[_RegistrySet] = None) -> None:
    """HKCU 写 CSXS.{8..12}\\PlayerDebugMode=1, 允许加载未签名扩展。"""
    if _setter is None:
        if not HAS_WINREG:
            return

        def _setter(path: str, name: str, value: str) -> None:  # type: ignore[misc]
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    for v in CSXS_VERSIONS:
        _setter(rf"Software\Adobe\CSXS.{v}", "PlayerDebugMode", "1")
    log.info("已启用 CEP PlayerDebugMode（CSXS 8~12）")


def install_extension(appdata: Optional[Path] = None) -> Path:
    """把随包扩展复制到用户 CEP extensions 目录（幂等, 可重复覆盖更新）。

    Raises:
        FileNotFoundError: 随包扩展源缺失（打包异常）。
    """
    src = bundled_extension_dir()
    if not (src / "CSXS" / "manifest.xml").is_file():
        raise FileNotFoundError(f"CEP 扩展源缺失: {src}")
    dst = extension_install_dir(appdata)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    _enable_player_debug_mode()
    log.info("CEP 扩展已安装 → %s", dst)
    return dst


# ------------------------------------------------------------------ #
#  任务 / 状态文件
# ------------------------------------------------------------------ #
def write_task(videos: List[str], fps: float, max_frames: int, out_dir: str,
               prefix: str = "frame_", fmt: str = "jpg",
               range_mode: str = "full",
               appdata: Optional[Path] = None) -> Path:
    """生成面板任务文件（含幂等安装扩展, 确保任务与面板同目录）。"""
    install_extension(appdata)
    task = {
        "videos": [str(v).replace("\\", "/") for v in videos],
        "fps": float(fps),
        "max_frames": int(max_frames),
        "out_dir": str(out_dir).replace("\\", "/"),
        "prefix": prefix,
        "format": fmt,
        "range_mode": range_mode,
        "cancelled": False,
        "created_at": time.time(),
    }
    path = extension_install_dir(appdata) / TASK_FILE
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    status = extension_install_dir(appdata) / STATUS_FILE
    if status.exists():          # 清掉上一轮的完成状态, 避免 GUI 读到旧值
        status.unlink()
    log.info("PR 任务已生成: %d 个视频 · %s fps → %s",
             len(videos), fps, out_dir)
    return path


def read_task(appdata: Optional[Path] = None) -> Optional[dict]:
    path = extension_install_dir(appdata) / TASK_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cancel_task(appdata: Optional[Path] = None) -> bool:
    """把任务的 cancelled 置 true（面板 1s 轮询到此标志即停止）。"""
    task = read_task(appdata)
    if not task:
        return False
    task["cancelled"] = True
    path = extension_install_dir(appdata) / TASK_FILE
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return True


def read_status(appdata: Optional[Path] = None) -> Optional[dict]:
    """读取面板回写的导出状态; 无文件/损坏 → None。"""
    path = extension_install_dir(appdata) / STATUS_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------ #
#  启动
# ------------------------------------------------------------------ #
def launch_premiere(exe: str) -> bool:
    """分离进程启动 Premiere（已在运行时仅激活现有实例）。"""
    try:
        subprocess.Popen(
            [exe], close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP)
        log.info("已启动 Premiere: %s", exe)
        return True
    except OSError as exc:
        log.error("启动 Premiere 失败: %s", exc)
        return False
