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
from app.utils.security import (PathSecurityError, ensure_output_dir,
                                resolve_executable, scrubbed_env, system_tool)

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
        # H-05: 可执行文件与环境隔离。Blender 只允许绝对路径或 PATH 解析,
        # 且拒绝解析到 CWD; 子进程健康变量剥离 PYTHONPATH/PYTHONHOME。
        exe = resolve_executable(self.exe)
        if not exe:
            raise BlenderBridgeError(f"Blender 可执行文件不可用: {self.exe}")
        script = Path(script).expanduser().resolve()
        if not script.is_file():
            raise BlenderBridgeError(f"缺少桥接脚本: {script}")

        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(params, f, ensure_ascii=False)
            param_file = f.name

        cmd = [
            exe,
            "--background",
            "--factory-startup",          # 隔离用户插件/首选项, 保证可复现
            "--python", str(script),
            "--", param_file,
        ]
        log.info("Blender 命令: %s", subprocess.list2cmdline(cmd))
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        started = time.monotonic()
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=scrubbed_env(),
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

        elapsed = time.monotonic() - started
        log.info("Blender 退出码 %d, 耗时 %.1fs, 声明产物 %d 个",
                 code, elapsed, len(files))
        ok = code == 0 and any(DONE_RE.search(l) for l in lines[-40:])
        if not ok:
            tail = "\n".join(lines[-12:])
            raise BlenderBridgeError(f"Blender 脚本未完成 (退出码 {code}):\n{tail}")
        # 产物校验: 协议声明的路径必须真实存在且非零字节
        verified: List[Path] = []
        for f in files:
            try:
                if f.is_file() and f.stat().st_size > 0:
                    verified.append(f)
                else:
                    log.error("Blender 产物缺失或为 0 字节: %s", f)
            except OSError as exc:
                log.error("Blender 产物不可读: %s (%s)", f, exc)
        if not verified:
            raise BlenderBridgeError(
                f"Blender 退出码 0 但无有效产物 (声明 {len(files)} 个)")
        log.info("Blender 完成, 有效产物 %d 个: %s", len(verified),
                 ", ".join(f.name for f in verified))
        return True, verified, lines

    # ================================================================== #
    #  高层业务接口
    # ================================================================== #
    def postprocess_mesh(self, mesh_in: Path, out_dir: Path,
                         base_name: str = "usf_scene",
                         formats: List[str] = ("fbx", "glb", "blend"),
                         lod_ratios: List[float] = (1.0, 0.5, 0.25),
                         bake_ao: bool = False,
                         texture_size: int = 4096) -> List[Path]:
        """网格 → 清理/UV/材质/LOD → FBX(嵌纹理)/GLB/BLEND。

        这是「调用 Blender 进行网格转 FBX」的主入口, 对应
        scripts/blender/mesh_to_fbx.py。
        """
        out_dir = ensure_output_dir(out_dir)
        mesh_in = Path(mesh_in).expanduser().resolve()
        if not mesh_in.is_file():
            raise BlenderBridgeError(f"输入网格不存在: {mesh_in}")
        params = {
            "input": str(mesh_in),
            "out_dir": str(out_dir),
            "base_name": base_name,
            "formats": list(formats),
            "lod_ratios": list(lod_ratios),
            "bake_ao": bool(bake_ao),
            "texture_size": int(texture_size),
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
                subprocess.run([system_tool("taskkill.exe"), "/F", "/T",
                                "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
            else:
                proc.kill()
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
