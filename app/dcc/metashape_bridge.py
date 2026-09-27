"""Agisoft Metashape Professional 无头桥接（核心自动化脚本之二）。

调用契约:
    metashape.exe -r repair_mesh.py
参数经环境变量 USF_METASHAPE_PARAMS (JSON 文件路径) 传入 —— Metashape
对命令行尾部参数的传递行为随版本波动, 环境变量最稳。

注意: 脚本执行需要 Metashape **Professional** 授权 (Standard 版无脚本 API)。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.utils.paths import script_path
from app.utils.logger import get_logger

log = get_logger("METASHAPE")

PROGRESS_RE = re.compile(r"\[MSF\] PROGRESS (\d+)(?: (.*))?")
ERROR_RE = re.compile(r"\[MSF\] ERROR (.*)")

ProgressCb = Callable[[int, str], None]
LogCb = Callable[[str], None]


class MetashapeBridgeError(RuntimeError):
    pass


class MetashapeBridge:
    def __init__(self, exe: str, timeout_s: int = 14_400,
                 progress_cb: Optional[ProgressCb] = None,
                 log_cb: Optional[LogCb] = None,
                 stop_event: Optional[threading.Event] = None):
        self.exe = str(exe)
        self.timeout_s = timeout_s
        self.progress_cb = progress_cb
        self.log_cb = log_cb
        self.stop_event = stop_event or threading.Event()

    # ================================================================== #
    def repair_mesh(self, frames_dir: Path, mesh_out: Path,
                    face_count: int = 200_000,
                    with_texture: bool = True) -> Tuple[bool, List[str]]:
        """摄影测量补全: 对齐照片 → 深度图 → 稠密云 → 建模 → 洞穴修复 → 导出 OBJ。

        用于修复 3DGS 泊松网格的空洞 / 弱纹理区域。
        """
        params = {
            "frames_dir": str(frames_dir),
            "mesh_out": str(mesh_out),
            "face_count": face_count,
            "with_texture": with_texture,
        }
        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False)
            param_file = f.name

        script = script_path("metashape", "repair_mesh.py")
        if not script.exists():
            raise MetashapeBridgeError(f"缺少桥接脚本: {script}")

        env = os.environ.copy()
        env["USF_METASHAPE_PARAMS"] = param_file
        cmd = [self.exe, "-r", str(script)]
        log.info("Metashape 无头启动 (洞穴修复): %s 帧 → %s", frames_dir, mesh_out.name)

        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=creation)

        lines: List[str] = []
        error_msg = ""
        deadline = time.time() + self.timeout_s
        try:
            for line in proc.stdout or []:
                text = line.rstrip()
                lines.append(text)
                if self.log_cb:
                    self.log_cb(text)
                m = PROGRESS_RE.search(text)
                if m and self.progress_cb:
                    self.progress_cb(int(m.group(1)), m.group(2) or "")
                m = ERROR_RE.search(text)
                if m:
                    error_msg = m.group(1)
                if self.stop_event.is_set():
                    self._kill_tree(proc)
                    raise MetashapeBridgeError("用户取消 Metashape 修复")
                if time.time() > deadline:
                    self._kill_tree(proc)
                    raise MetashapeBridgeError(
                        f"Metashape 无响应超时 ({self.timeout_s}s), 已终止")
            code = proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                self._kill_tree(proc)
            try:
                Path(param_file).unlink(missing_ok=True)
            except OSError:
                pass

        if code != 0 or error_msg:
            raise MetashapeBridgeError(
                error_msg or f"Metashape 脚本失败 (退出码 {code})。"
                f"请确认已安装 Professional 版且授权有效。")
        return True, lines

    # ================================================================== #
    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
            else:
                proc.kill()
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
