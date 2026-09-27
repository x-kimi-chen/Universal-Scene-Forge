"""GPU 能力探测（厂商无关: NVIDIA / AMD / Intel）。

用途:
- NVIDIA: nvidia-smi 细节 + CUDA/COLMAP-GPU/3DGS 训练全加速;
- AMD (Radeon/Arc 挂靠) 与 Intel (Arc/核显): Metashape 走 Vulkan/OpenCL 照常
  加速; COLMAP 自动回退 CPU 版本; 3DGS 训练不可用(上游 torch 栈仅 CUDA),
  导出走 Metashape 兜底链。
"""
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
    vendor: str = "none"               # nvidia | amd | intel | none
    name: str = ""
    driver: str = ""
    vram_mb: int = 0
    torch_cuda: bool = False           # 当前解释器内 torch 是否可用
    source: str = ""

    @property
    def is_nvidia(self) -> bool:
        return self.vendor == "nvidia"

    def summary(self) -> str:
        if not self.available:
            return "未检测到独立 GPU（COLMAP/网格走 CPU 模式，速度较慢）"
        if self.vendor == "nvidia":
            return (f"{self.name} | {self.vram_mb}MB | 驱动 {self.driver}"
                    f" | CUDA 加速: {'可用' if self.torch_cuda else '检测中'}")
        return (f"{self.name} ({self.vendor.upper()}) | {self.vram_mb}MB"
                f" | COLMAP 走 CPU, Metashape 照常 GPU 加速; "
                f"3DGS 训练需 NVIDIA (导出走 Metashape 兜底)")

    @property
    def colmap_gpu_ok(self) -> bool:
        """COLMAP 的 CUDA SIFT 仅支持 NVIDIA; 其余厂商用 CPU 版本/参数。"""
        return self.vendor == "nvidia"


def _vendor_from_name(name: str) -> str:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "quadro" in n or "rtx" in n \
            or "gtx" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n:
        return "amd"
    if "intel" in n or "arc" in n or "iris" in n or "uhd" in n:
        return "intel"
    return "none"


def _wmi_primary_gpu() -> tuple[str, int]:
    """WMI 枚举显卡, 返回 (名称, 显存MB)。优先取非基本显示适配器。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Select-Object Name, AdapterRAM | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20,
            creationflags=_NO_WINDOW())
        if out.returncode != 0 or not out.stdout.strip():
            return "", 0
        import json
        data = json.loads(out.stdout)
        cards = data if isinstance(data, list) else [data]
        # 优先独立卡: 名称含 NVIDIA/AMD/Radeon/Arc 的第一个
        cards.sort(key=lambda c: 0 if _vendor_from_name(
            str(c.get("Name", ""))) in ("nvidia", "amd") else 1)
        for c in cards:
            name = str(c.get("Name", "") or "")
            if not name:
                continue
            ram = int(c.get("AdapterRAM") or 0)
            # AdapterRAM 是 32 位有符号, 大显存会溢出; 名称解析兜底
            vram = max(0, ram) // (1 << 20)
            return name, vram
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return "", 0


def probe() -> GpuInfo:
    info = GpuInfo()

    # 1) NVIDIA: nvidia-smi 提供最准确的名称/显存/驱动
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
                info.vendor = "nvidia"
                info.name = name
                info.vram_mb = int("".join(ch for ch in vram if ch.isdigit()) or 0)
                info.driver = driver
                info.source = "nvidia-smi"
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass

    # 2) 非 NVIDIA (或探测失败): WMI 枚举, 覆盖 AMD / Intel / 核显
    if not info.available:
        name, vram = _wmi_primary_gpu()
        if name:
            info.available = True
            info.vendor = _vendor_from_name(name)
            info.name = name
            info.vram_mb = vram
            info.source = "wmi"

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
