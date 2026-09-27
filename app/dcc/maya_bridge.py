"""Autodesk Maya 无头桥接（模板实现）。

优先使用 mayapy.exe 独立解释器:
    mayapy <script>    (脚本内 maya.standalone.initialize())
其次 maya.exe -batch -command "python(...)"。
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from app.utils.logger import get_logger

log = get_logger("MAYA")

LogCb = Callable[[str], None]


class MayaBridgeError(RuntimeError):
    pass


class MayaBridge:
    def __init__(self, exe: str, timeout_s: int = 3600,
                 log_cb: Optional[LogCb] = None,
                 stop_event: Optional[threading.Event] = None):
        """exe 允许是 mayapy.exe 或 maya.exe。"""
        self.exe = str(exe)
        self.timeout_s = timeout_s
        self.log_cb = log_cb
        self.stop_event = stop_event or threading.Event()

    # ================================================================== #
    def run_mel(self, mel: str) -> List[str]:
        """maya.exe -batch 模式执行 MEL 片段（示例: FBX 导出）。"""
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [self.exe, "-batch", "-command", mel]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=creation)
        lines: List[str] = []
        deadline = time.time() + self.timeout_s
        for line in proc.stdout or []:
            lines.append(line.rstrip())
            if self.log_cb:
                self.log_cb(lines[-1])
            if self.stop_event.is_set() or time.time() > deadline:
                proc.kill()
                raise MayaBridgeError("Maya 执行被取消或超时")
        code = proc.wait(timeout=30)
        if code != 0:
            raise MayaBridgeError(f"Maya 退出码 {code}: {mel[:80]}")
        return lines

    # ================================================================== #
    @staticmethod
    def build_fbx_export_mel(obj_path: Path, fbx_out: Path) -> str:
        """生成「OBJ 导入 → FBX 导出」的 MEL 命令串。"""
        return (
            f'loadPlugin "fbxmaya"; '
            f'file -import -type "OBJ" -ignoreVersion -ra true -options "mo=1" '
            f'"{obj_path.as_posix()}"; '
            f'file -force -options "v=0;" -typ "FBX export" -pr -es '
            f'"{fbx_out.as_posix()}";'
        )
