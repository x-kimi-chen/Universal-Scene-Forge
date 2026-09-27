# -*- coding: utf-8 -*-
"""Foundry Modo 无头桥接（OBJ 导入 → FBX 导出, 导出降级链的一环）。

调用方式: modo.exe -command:"python(\"<生成的脚本>\")"
脚本内容按次生成(路径内联), 规避命令行转义差异。
未装 modo 时导出链自动跳过本环节。
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from app.utils.logger import get_logger

log = get_logger("MODO")

LogCb = Callable[[str], None]


class ModoBridgeError(RuntimeError):
    pass


class ModoBridge:
    def __init__(self, exe: str, timeout_s: int = 3600,
                 log_cb: Optional[LogCb] = None,
                 stop_event: Optional[threading.Event] = None):
        self.exe = str(exe)
        self.timeout_s = timeout_s
        self.log_cb = log_cb
        self.stop_event = stop_event or threading.Event()

    # ================================================================== #
    def export_fbx(self, mesh_obj: Path, fbx_out: Path) -> List[Path]:
        """OBJ 导入 → FBX 导出（modo Python 命令模式）。"""
        mesh_obj, fbx_out = Path(mesh_obj), Path(fbx_out)
        fbx_out.parent.mkdir(parents=True, exist_ok=True)

        py = fbx_out.parent / "usf_modo_export.py"
        py.write_text(
            "import lx\n"
            f"lx.eval('!import.obj locate {{{mesh_obj.as_posix()}}}')\n"
            f"lx.eval('!export.fbx filename {{{fbx_out.as_posix()}}} persist false')\n"
            "lx.eval('!scene.close')\n",
            encoding="utf-8")

        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [self.exe, "-command:", f'python("{py.as_posix()}")']
        log.info("Modo 无头启动: %s → %s", mesh_obj.name, fbx_out.name)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creation)
        lines: List[str] = []
        deadline = time.time() + self.timeout_s
        try:
            for line in proc.stdout or []:
                lines.append(line.rstrip())
                if self.log_cb:
                    self.log_cb(lines[-1])
                if self.stop_event.is_set() or time.time() > deadline:
                    proc.kill()
                    raise ModoBridgeError("Modo 执行被取消或超时")
            code = proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
            py.unlink(missing_ok=True)

        if code != 0 or not fbx_out.exists():
            raise ModoBridgeError(
                f"Modo FBX 导出失败 (退出码 {code}), 未产出 {fbx_out.name}")
        log.info("Modo 完成: %s", fbx_out)
        return [fbx_out]
