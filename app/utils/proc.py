# -*- coding: utf-8 -*-
"""子进程生命周期的统一入口。

各 DCC 桥接原先各自实现了一份 `_kill_tree`, 且其中多数直接调用裸命令
`taskkill`, 依赖 PATH 解析。Windows 下 PATH 可被当前目录或用户目录中的
同名程序劫持, 与 `app/utils/security.py` 的"外部程序只走绝对路径"约定
不一致。此模块把终止逻辑收敛到一处, 统一使用 System32 下的绝对路径。
"""
from __future__ import annotations

import subprocess
import sys

from app.utils.logger import get_logger
from app.utils.security import system_tool

log = get_logger("PROC")


def kill_tree(proc: subprocess.Popen, timeout_s: int = 15) -> None:
    """终止进程及其子进程；任何失败都回退到 proc.kill(), 不向上抛异常。

    桥接的看门狗与用户取消路径都会调用本函数, 因此这里必须保证"不抛异常"：
    终止失败不应掩盖原始的失败原因。
    """
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                [system_tool("taskkill.exe"), "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=timeout_s)
        else:
            proc.kill()
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("taskkill 失败, 回退到 kill(): %s", exc)
        try:
            proc.kill()
        except OSError:
            pass
