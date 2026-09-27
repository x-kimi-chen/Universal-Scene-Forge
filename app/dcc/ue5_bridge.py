"""UE5 实时预览推流桥接。

策略（按依赖从低到高三选一）:
A. Editor-Cmd + pythonscript 命令行子进程导入（默认实现, 需工程启用
   "Python Editor Script Plugin"）——重建完成后把中间 .fbx/.obj 推入
   /Game/USF 并自动保存 .uasset。
B. Interchange 监视目录（工程侧开启 Auto-Import）——复制即导入。
C. Live Link（扩展点, 适合逐帧预览, 不在本模板范围）。
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

log = get_logger("UE5")

PROGRESS_RE = re.compile(r"\[UEF\] PROGRESS (\d+)(?: (.*))?")
LogCb = Callable[[str], None]


class UE5BridgeError(RuntimeError):
    pass


class UE5Bridge:
    def __init__(self, editor_cmd: str, uproject: str, timeout_s: int = 900,
                 log_cb: Optional[LogCb] = None,
                 stop_event: Optional[threading.Event] = None):
        self.editor_cmd = str(editor_cmd)   # UnrealEditor-Cmd.exe
        self.uproject = str(uproject)
        self.timeout_s = timeout_s
        self.log_cb = log_cb
        self.stop_event = stop_event or threading.Event()

    # ================================================================== #
    def import_files(self, files: List[Path],
                     destination: str = "/Game/USF") -> List[str]:
        """把 FBX/OBJ 推入 UE5 并自动保存为 .uasset。返回导入的资产路径。"""
        if not Path(self.uproject).exists():
            raise UE5BridgeError(f"UE5 工程不存在: {self.uproject}")
        payload = {
            "files": [str(f) for f in files],
            "destination": destination,
        }
        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            list_file = f.name

        script = script_path("ue5", "import_and_save.py")
        if not script.exists():
            raise UE5BridgeError(f"缺少桥接脚本: {script}")

        env = os.environ.copy()
        env["USF_UE5_IMPORT_LIST"] = list_file
        cmd = [
            self.editor_cmd,
            self.uproject,
            "-run=pythonscript",
            f"-script={script.as_posix()}",
            "-stdout",            # 输出重定向到控制台
            "-unattended",
            "-nosplash",
            "-nopause",
        ]
        log.info("UE5 推流: %d 个文件 → %s", len(files), destination)

        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=creation)

        imported: List[str] = []
        lines: List[str] = []
        deadline = time.time() + self.timeout_s
        try:
            for line in proc.stdout or []:
                text = line.rstrip()
                lines.append(text)
                if self.log_cb:
                    self.log_cb(text)
                if "[UEF] ASSET " in text:
                    imported.append(text.split("[UEF] ASSET ", 1)[1].strip())
                if self.stop_event.is_set():
                    self._kill_tree(proc)
                    raise UE5BridgeError("用户取消 UE5 推流")
                if time.time() > deadline:
                    self._kill_tree(proc)
                    raise UE5BridgeError(
                        f"UE5 无响应超时 ({self.timeout_s}s), 已终止")
            code = proc.wait(timeout=120)
        finally:
            if proc.poll() is None:
                self._kill_tree(proc)
            try:
                Path(list_file).unlink(missing_ok=True)
            except OSError:
                pass

        if code != 0:
            tail = "\n".join(lines[-10:])
            raise UE5BridgeError(
                f"UE5 导入失败 (退出码 {code})。"
                f"请确认工程已启用 Python Editor Script Plugin。\n{tail}")
        log.info("UE5 推流完成: %s", imported or destination)
        return imported

    # ================================================================== #
    def push_intermediate(self, mesh_file: Path) -> Optional[List[str]]:
        """重建中间结果推流（容错包装: 任何失败仅记日志返回 None）。"""
        try:
            return self.import_files([mesh_file])
        except UE5BridgeError as exc:
            log.warning("UE5 预览推送跳过: %s", exc)
            return None

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
