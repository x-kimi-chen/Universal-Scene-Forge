"""Autodesk 3ds Max 无头桥接。

调用契约:
    3dsmaxbatch.exe <generated.ms>        (Max 2018+ 官方批处理解释器)

- 参数以占位符替换进 scripts/max/obj_to_fbx.ms 模板后落盘临时 .ms
  (MAXScript 无成熟的 JSON 解析, 内嵌字面量最可靠)
- 3dsmaxbatch 不把 MAXScript format 输出接到 stdout → 协议行改走结果文件,
  本端增量轮询解析（进度仍可流式回传）
- 脚本端进度协议:  [USF] PROGRESS <0-100> <msg>
- 产物回传协议:    [USF] FILE <path>
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

log = get_logger("MAX")

PROGRESS_RE = re.compile(r"\[USF\] PROGRESS (\d+)(?: (.*))?")
FILE_RE = re.compile(r"\[USF\] FILE (.+)")
DONE_RE = re.compile(r"\[USF\] DONE")
ERROR_RE = re.compile(r"\[USF\] ERROR (.+)")

ProgressCb = Callable[[int, str], None]
LogCb = Callable[[str], None]


class MaxBridgeError(RuntimeError):
    pass


class MaxBridge:
    def __init__(self, exe: str, timeout_s: int = 3600,
                 progress_cb: Optional[ProgressCb] = None,
                 log_cb: Optional[LogCb] = None,
                 stop_event: Optional[threading.Event] = None):
        self.exe = str(exe)
        self.timeout_s = timeout_s
        self.progress_cb = progress_cb
        self.log_cb = log_cb
        self.stop_event = stop_event or threading.Event()

    # ================================================================== #
    def _batch_exe(self) -> str:
        """3dsmax.exe 同目录下的官方批处理解释器 3dsmaxbatch.exe。"""
        batch = Path(self.exe).resolve().parent / "3dsmaxbatch.exe"
        if not batch.exists():
            raise MaxBridgeError(f"未找到 3dsmaxbatch.exe: {batch}")
        return str(batch)

    # ================================================================== #
    def run_script(self, ms_file: Path, result_file: Optional[Path] = None,
                   timeout_s: Optional[int] = None) -> Tuple[bool, List[Path], List[str]]:
        """执行 MAXScript 文件；返回 (ok, 产物列表, 全部协议行)。

        - stdout 由后台线程持续排空（3dsmaxbatch 行稀疏, 防主循环卡死）
        - 协议行从 result_file 增量轮询获得
        """
        cmd = [self._batch_exe(), str(ms_file)]
        log.info("3ds Max 无头启动: %s", ms_file.name)
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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
                    raise MaxBridgeError("用户取消 3ds Max 转换")
                if time.time() > deadline:
                    raise MaxBridgeError(
                        f"3ds Max 无响应超时 ({self.timeout_s}s), 已终止进程树")
                if result_file is not None:
                    offset = self._consume_result(
                        result_file, offset, files, protocol)
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
            code = proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                self._kill_tree(proc)
            reader.join(timeout=5)
            if result_file is not None:
                self._consume_result(result_file, offset, files, protocol)

        if code != 0:
            tail = "\n".join(stdout_lines[-12:])
            raise MaxBridgeError(f"3ds Max 退出码 {code}:\n{tail}")
        ok = any(DONE_RE.search(l) for l in protocol)
        if not ok:
            err = next((ERROR_RE.search(l).group(1) for l in protocol
                        if ERROR_RE.search(l)), "")
            tail = "\n".join(stdout_lines[-12:])
            raise MaxBridgeError(
                f"3ds Max 脚本未完成: {err or '未收到 DONE 协议'}\n{tail}")
        log.info("3ds Max 完成, 产物 %d 个: %s", len(files),
                 ", ".join(f.name for f in files))
        return True, files, protocol

    # ------------------------------------------------------------------ #
    def _consume_result(self, result_file: Path, offset: int,
                        files: List[Path], protocol: List[str]) -> int:
        """增量读取结果文件新增的完整行, 解析进 files/protocol。

        MAXScript 以本机 ANSI 写文件 → ASCII 协议行 UTF-8/GBK 兼容。
        返回新的字节偏移（停在最后一个换行符上, 半行留待下次）。
        """
        try:
            with result_file.open("rb") as f:
                f.seek(offset)
                chunk = f.read()
                if not chunk:
                    return offset
                last_nl = chunk.rfind(b"\n")
                if last_nl < 0:
                    return offset
                offset += last_nl + 1
                text = chunk[:last_nl].decode("utf-8", errors="replace")
        except OSError:
            return offset
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            protocol.append(line)
            m = PROGRESS_RE.search(line)
            if m and self.progress_cb:
                self.progress_cb(int(m.group(1)), m.group(2) or "")
            m = FILE_RE.search(line)
            if m:
                files.append(Path(m.group(1).strip()))
        return offset

    # ================================================================== #
    #  高层业务接口
    # ================================================================== #
    def export_fbx(self, mesh_in: Path, fbx_out: Path) -> Path:
        """OBJ → FBX（3ds Max 权威 FBX 导出器, 嵌材质）。"""
        fbx_out.parent.mkdir(parents=True, exist_ok=True)
        template = script_path("max", "obj_to_fbx.ms")
        if not template.exists():
            raise MaxBridgeError(f"缺少桥接脚本模板: {template}")
        # MAXScript 字符串字面量: 反斜杠/引号转义
        def _lit(p: Path) -> str:
            return str(p.resolve()).replace("\\", "\\\\").replace('"', '\\"')

        ms_text = template.read_text(encoding="utf-8")
        tmp_dir = Path(tempfile.mkdtemp(prefix="usf_max_"))
        ms_file = tmp_dir / "obj_to_fbx.ms"
        result_file = tmp_dir / "result.log"
        ms_text = (ms_text
                   .replace("__USF_INPUT__", _lit(mesh_in))
                   .replace("__USF_OUTPUT__", _lit(fbx_out))
                   .replace("__USF_RESULT__", _lit(result_file)))
        ms_file.write_text(ms_text, encoding="utf-8")
        try:
            _, files, _ = self.run_script(ms_file, result_file=result_file)
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        for f in files:
            if f.suffix.lower() == ".fbx":
                return f
        if fbx_out.exists():
            return fbx_out
        raise MaxBridgeError("3ds Max 未产出 FBX")

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
