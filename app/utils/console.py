# -*- coding: utf-8 -*-
"""命令行模式的控制台保障。

PyInstaller 以 `console=False` 打包后, 解释器没有标准输出, `sys.stdout`
为 None。此时 `python main.py --check-deps` 能正常打印, 而打包后的
`UniversalSceneForge.exe --check-deps` 只会静默退出——命令被接收, 报告
却无处可写。此模块在进入命令行模式前把标准流接回来:

1. 先尝试附加到调用方控制台（从 cmd/PowerShell 启动时最自然）;
2. 附加失败（双击启动、父进程无控制台）时新建一个控制台窗口;
3. 两者都失败时把输出重定向到日志文件, 由调用方告知文件位置。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

_ATTACH_PARENT_PROCESS = -1
_ERROR_ACCESS_DENIED = 5


def _attach_or_alloc() -> bool:
    import ctypes

    k32 = ctypes.windll.kernel32
    if k32.AttachConsole(_ATTACH_PARENT_PROCESS):
        return True
    if ctypes.get_last_error() == _ERROR_ACCESS_DENIED:
        # 已附加到某个控制台, 无需再建
        return True
    return bool(k32.AllocConsole())


def _reopen_streams() -> None:
    for name, mode in (("stdout", "w"), ("stderr", "w")):
        stream = open("CONOUT$", mode, encoding="utf-8",
                      errors="backslashreplace", buffering=1)
        setattr(sys, name, stream)


def _fallback_file() -> Path:
    """无控制台可用时的落盘位置: 用户目录下的 logs/（冻结模式可写）。"""
    base = Path.home() / ".universal_scene_forge" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / time.strftime("cli_%Y%m%d_%H%M%S.txt")


def ensure_console() -> Optional[Path]:
    """确保控制台可写。

    返回 None 表示已接回控制台（输出照常打印）;
    返回 Path 表示控制台不可用, 已把 stdout/stderr 重定向到该文件,
    调用方应在结束时提示该路径。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None            # 源码运行 / 已有控制台

    if sys.platform == "win32":
        try:
            if _attach_or_alloc():
                _reopen_streams()
                return None
        except (OSError, AttributeError):
            pass

    path = _fallback_file()
    stream = open(path, "w", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    return path


def notify_output_saved(path: Path) -> None:
    """无控制台时用消息框告知报告落盘位置（窗口程序唯一的可见通道）。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None,
            f"当前环境没有可用控制台，报告已写入：\n{path}",
            "通用场景锻造系统", 0x40)
    except Exception:  # noqa: BLE001 —— 提示失败不影响退出码
        pass
