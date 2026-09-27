"""CUDA / GPU 能力探测。"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

from app.utils.logger import get_logger

log = get_logger("GPU")


@dataclass
class GpuInfo:
    available: bool = False
    name: str = ""
    driver: str = ""
    vram_mb: int = 0
    torch_cuda: bool = False           # 当前解释器内 torch 是否可用
    source: str = ""

    def summary(self) -> str:
        if not self.available:
            return "未检测到 NVIDIA GPU（3DGS 将退化为 CPU 模式，极慢）"
        return f"{self.name} | {self.vram_mb}MB | 驱动 {self.driver}"


def probe() -> GpuInfo:
    info = GpuInfo()
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
                creationflags=_NO_WINDOW())
            if out.returncode == 0 and out.stdout.strip():
                name, vram, driver = [c.strip() for c in
                                      out.stdout.strip().splitlines()[0].split(",")]
                info.available = True
                info.name = name
                info.vram_mb = int("".join(ch for ch in vram if ch.isdigit()) or 0)
                info.driver = driver
                info.source = "nvidia-smi"
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    try:  # torch 只在训练环境中可用，GUI 壳缺 torch 属正常
        import torch  # noqa: WPS433
        info.torch_cuda = torch.cuda.is_available()
    except ImportError:
        pass
    log.info("GPU 探测: %s", info.summary())
    return info


def _NO_WINDOW() -> int:
    import sys
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
