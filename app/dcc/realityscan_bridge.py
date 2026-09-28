# -*- coding: utf-8 -*-
"""RealityScan / RealityCapture 无头桥接骨架（0.9.6 前置）。

Epic 的 RealityScan(2.x)/RealityCapture(1.4+) 支持命令行无头重建:
    RealityScan.exe -set <变量> <值> ... -addFolder/<images>
        -calculate* -exportModel <格式> <路径>

与 Metashape 桥接同构: 生成命令脚本 → 子进程执行 → 校验产物。
骨架状态: 需在装有 RealityScan 的机器上完成命令与许可联调
(需 Epic 账号授权); 未安装时优雅跳过, 不影响主流程。

官方 CLI 参考变量(以安装版本帮助为准):
    REALITYCAPTURE_LICENSE / -set InstanceName ...
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from app.utils.logger import get_logger

log = get_logger("RSCAN")

LogCb = Callable[[str], None]


class RealityScanBridgeError(RuntimeError):
    pass


class RealityScanBridge:
    def __init__(self, exe: str, timeout_s: int = 14_400,
                 log_cb: Optional[LogCb] = None,
                 stop_event: Optional[threading.Event] = None):
        self.exe = str(exe)
        self.timeout_s = timeout_s
        self.log_cb = log_cb
        self.stop_event = stop_event or threading.Event()

    # ================================================================== #
    def reconstruct_and_export(self, images_dir: Path,
                               model_out: Path) -> Path:
        """图像目录 → 对齐/重建/网格化 → 导出 OBJ。

        骨架状态: 命令序列按 RealityCapture CLI 惯例编写,
        需在装有 RealityScan 的机器上联调（许可/参数名随版本变化）。
        """
        images_dir, model_out = Path(images_dir), Path(model_out)
        model_out.parent.mkdir(parents=True, exist_ok=True)

        script = model_out.parent / "usf_realityscan.cli"
        script.write_text("\n".join([
            f"-set WorkingFolder {images_dir.as_posix()}/",
            "-addFolder %WorkingFolder%",
            "-calculateAlignment",
            "-calculateDepthMaps",
            "-calculateModel",
            f"-exportModel OBJ {model_out.as_posix()}",
            "-quit",
        ]) + "\n", encoding="utf-8")

        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cmd = [self.exe, str(script)]
        log.info("RealityScan 无头启动: %s → %s",
                 images_dir.name, model_out.name)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creation)
        lines: List[str] = []
        deadline = time.time() + self.timeout_s
        try:
            for line in proc.stdout or []:
                text = line.rstrip()
                lines.append(text)
                if self.log_cb:
                    self.log_cb(text)
                if self.stop_event.is_set() or time.time() > deadline:
                    proc.kill()
                    raise RealityScanBridgeError(
                        "RealityScan 被取消或超时")
            code = proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
            script.unlink(missing_ok=True)

        if code != 0 or not model_out.exists():
            tail = "\n".join(lines[-8:])
            raise RealityScanBridgeError(
                f"RealityScan 重建/导出失败 (退出码 {code})。"
                f"请确认授权有效且 CLI 参数与安装版本匹配。\n{tail}")
        log.info("RealityScan 完成: %s", model_out)
        return model_out
