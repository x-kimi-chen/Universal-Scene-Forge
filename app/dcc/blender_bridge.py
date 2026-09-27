"""Blender 无头桥接（核心自动化脚本之一）。

调用契约:
    blender --background --factory-startup --python mesh_to_fbx.py -- params.json

- 参数经 UTF-8 JSON 文件传递（规避命令行引号/中文转义问题）
- 脚本端进度协议:  [USF] PROGRESS <0-100> <msg>
- 产物回传协议:    [USF] FILE <abs path>
- 看门狗:          超时/取消 → taskkill /F /T 杀进程树
"""
from __future__ import annotations

import json
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

log = get_logger("BLENDER")

PROGRESS_RE = re.compile(r"\[USF\] PROGRESS (\d+)(?: (.*))?")
FILE_RE = re.compile(r"\[USF\] FILE (.+)")
DONE_RE = re.compile(r"\[USF\] DONE")

ProgressCb = Callable[[int, str], None]          # (percent, msg)
LogCb = Callable[[str], None]


class BlenderBridgeError(RuntimeError):
    pass


class BlenderBridge:
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
    #  底层执行器
    # ================================================================== #
    def run_script(self, script: Path, params: dict,
                   timeout_s: Optional[int] = None) -> Tuple[bool, List[Path], List[str]]:
        """执行 Blender 内部脚本；返回 (ok, 产物列表, 全部日志行)。"""
        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False)
            param_file = f.name

        cmd = [
            self.exe,
            "--background",
            "--factory-startup",          # 隔离用户插件/首选项, 保证可复现
            "--python", str(script),
            "--", param_file,
        ]
        log.info("Blender 无头启动: %s", script.name)
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creation)

        files: List[Path] = []
        lines: List[str] = []
        deadline = time.time() + (timeout_s or self.timeout_s)
        try:
            for line in proc.stdout or []:
                text = line.rstrip()
                lines.append(text)
                if self.log_cb:
                    self.log_cb(text)
                m = PROGRESS_RE.search(text)
                if m and self.progress_cb:
                    self.progress_cb(int(m.group(1)), m.group(2) or "")
                m = FILE_RE.search(text)
                if m:
                    files.append(Path(m.group(1).strip()))
                if self.stop_event.is_set():
                    self._kill_tree(proc)
                    raise BlenderBridgeError("用户取消 Blender 后处理")
                if time.time() > deadline:
                    self._kill_tree(proc)
                    raise BlenderBridgeError(
                        f"Blender 无响应超时 ({self.timeout_s}s), 已终止进程树")
            code = proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                self._kill_tree(proc)
            try:
                Path(param_file).unlink(missing_ok=True)
            except OSError:
                pass

        ok = code == 0 and any(DONE_RE.search(l) for l in lines[-40:])
        if not ok:
            tail = "\n".join(lines[-12:])
            raise BlenderBridgeError(f"Blender 脚本未完成 (退出码 {code}):\n{tail}")
        log.info("Blender 完成, 产物 %d 个: %s", len(files),
                 ", ".join(f.name for f in files))
        return True, files, lines

    # ================================================================== #
    #  高层业务接口
    # ================================================================== #
    def postprocess_mesh(self, mesh_in: Path, out_dir: Path,
                         base_name: str = "usf_scene",
                         formats: List[str] = ("fbx", "glb", "blend"),
                         lod_ratios: List[float] = (1.0, 0.5, 0.25),
                         bake_ao: bool = False) -> List[Path]:
        """网格 → 清理/UV/材质/LOD → FBX(嵌纹理)/GLB/BLEND。

        这是「调用 Blender 进行网格转 FBX」的主入口, 对应
        scripts/blender/mesh_to_fbx.py。
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        params = {
            "input": str(mesh_in),
            "out_dir": str(out_dir),
            "base_name": base_name,
            "formats": list(formats),
            "lod_ratios": list(lod_ratios),
            "bake_ao": bool(bake_ao),
        }
        script = script_path("blender", "mesh_to_fbx.py")
        if not script.exists():
            raise BlenderBridgeError(f"缺少桥接脚本: {script}")
        _, files, _ = self.run_script(script, params)
        return files

    def export_fbx_only(self, mesh_in: Path, fbx_out: Path) -> Path:
        """极简入口: 仅转换单个网格为 FBX（带嵌纹理）。"""
        out_dir = fbx_out.parent
        files = self.postprocess_mesh(mesh_in, out_dir,
                                      base_name=fbx_out.stem,
                                      formats=["fbx"], lod_ratios=[1.0])
        for f in files:
            if f.suffix.lower() == ".fbx":
                return f
        raise BlenderBridgeError("Blender 未产出 FBX")

    def save_blend(self, mesh_in: Path, blend_out: Path) -> Path:
        files = self.postprocess_mesh(mesh_in, blend_out.parent,
                                      base_name=blend_out.stem,
                                      formats=["blend"], lod_ratios=[1.0])
        for f in files:
            if f.suffix.lower() == ".blend":
                return f
        raise BlenderBridgeError("Blender 未产出 .blend")

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
