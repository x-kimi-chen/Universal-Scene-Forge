"""SideFX Houdini 无头桥接（hython 解释器）。

调用契约:
    hython.exe <generated.py>

- 参数以占位符替换进 scripts/houdini/obj_to_fbx.py 模板后落盘临时脚本
  (hython 参数传递在不同版本下行为不一, 内嵌字面量最可靠)
- 脚本端进度协议:  [USF] PROGRESS <0-100> <msg>
- 产物回传协议:    [USF] FILE <abs path>
- 看门狗:          超时/取消 → taskkill /F /T 杀进程树
"""
from __future__ import annotations

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

log = get_logger("HOUDINI")

PROGRESS_RE = re.compile(r"\[USF\] PROGRESS (\d+)(?: (.*))?")
FILE_RE = re.compile(r"\[USF\] FILE (.+)")
DONE_RE = re.compile(r"\[USF\] DONE")
ERROR_RE = re.compile(r"\[USF\] ERROR (.+)")

ProgressCb = Callable[[int, str], None]
LogCb = Callable[[str], None]


class HoudiniBridgeError(RuntimeError):
    pass


class HoudiniBridge:
    def __init__(self, exe: str, timeout_s: int = 1800,
                 progress_cb: Optional[ProgressCb] = None,
                 log_cb: Optional[LogCb] = None,
                 stop_event: Optional[threading.Event] = None):
        self.exe = str(exe)
        self.timeout_s = timeout_s
        self.progress_cb = progress_cb
        self.log_cb = log_cb
        self.stop_event = stop_event or threading.Event()

    # ================================================================== #
    def run_script(self, py_file: Path,
                   timeout_s: Optional[int] = None) -> Tuple[bool, List[Path], List[str]]:
        """执行 hython 脚本；返回 (ok, 产物列表, 全部日志行)。

        - stdout 由后台线程持续排空（进程静默期不阻塞看门狗）
        - 主循环轮询: 取消 / 超时 / 退出状态
        """
        cmd = [self.exe, str(py_file)]
        log.info("Houdini hython 启动: %s", py_file.name)
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creation)

        stdout_lines: List[str] = []

        def _drain() -> None:
            try:
                for line in proc.stdout or []:
                    stdout_lines.append(line.rstrip())
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=_drain, daemon=True)
        reader.start()

        files: List[Path] = []
        protocol: List[str] = []
        offset = 0
        deadline = time.time() + (timeout_s or self.timeout_s)
        try:
            while True:
                if self.stop_event.is_set():
                    raise HoudiniBridgeError("用户取消 Houdini 转换")
                if time.time() > deadline:
                    raise HoudiniBridgeError(
                        f"Houdini 无响应超时 ({timeout_s or self.timeout_s}s), 已终止进程树")
                offset = self._consume_lines(stdout_lines, offset, files, protocol)
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
            code = proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                self._kill_tree(proc)
            reader.join(timeout=5)
            offset = self._consume_lines(stdout_lines, offset, files, protocol)

        err = next((ERROR_RE.search(l).group(1) for l in protocol
                    if ERROR_RE.search(l)), "")
        ok = code == 0 and any(DONE_RE.search(l) for l in protocol[-60:])
        if not ok:
            tail = "\n".join((stdout_lines + protocol)[-12:])
            raise HoudiniBridgeError(
                f"Houdini 脚本未完成 (退出码 {code}): {err or '未收到 DONE 协议'}\n{tail}")
        log.info("Houdini 完成, 产物 %d 个: %s", len(files),
                 ", ".join(f.name for f in files))
        return True, files, stdout_lines + protocol

    # ------------------------------------------------------------------ #
    def _consume_lines(self, buffer: List[str], offset: int,
                       files: List[Path], protocol: List[str]) -> int:
        """增量解析 stdout 缓冲区新行, 解析进 files/protocol/回调。"""
        while offset < len(buffer):
            text = buffer[offset]
            offset += 1
            if self.log_cb:
                self.log_cb(text)
            m = PROGRESS_RE.search(text)
            if m and self.progress_cb:
                self.progress_cb(int(m.group(1)), m.group(2) or "")
            m = FILE_RE.search(text)
            if m:
                files.append(Path(m.group(1).strip()))
            if "[USF]" in text:
                protocol.append(text)
        return offset

    # ================================================================== #
    #  高层业务接口
    # ================================================================== #
    def export_fbx(self, mesh_in: Path, fbx_out: Path) -> Path:
        """OBJ → FBX（Houdini Filmbox ROP, 降级链末端之一）。"""
        fbx_out.parent.mkdir(parents=True, exist_ok=True)
        template = script_path("houdini", "obj_to_fbx.py")
        if not template.exists():
            raise HoudiniBridgeError(f"缺少桥接脚本模板: {template}")

        def _lit(p: Path) -> str:
            # 模板用 raw 字符串: 统一转正斜杠规避反斜杠转义问题
            return str(p.resolve()).replace("\\", "/").replace('"', "")

        py_text = template.read_text(encoding="utf-8")
        py_text = (py_text
                   .replace("__USF_INPUT__", _lit(mesh_in))
                   .replace("__USF_OUTPUT__", _lit(fbx_out)))
        with tempfile.NamedTemporaryFile(
                "w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(py_text)
            py_file = Path(f.name)
        try:
            _, files, _ = self.run_script(py_file)
        finally:
            py_file.unlink(missing_ok=True)
        for f in files:
            if f.suffix.lower() == ".fbx":
                return f
        if fbx_out.exists():
            return fbx_out
        raise HoudiniBridgeError("Houdini 未产出 FBX")

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
