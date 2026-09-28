# -*- coding: utf-8 -*-
"""主体遮罩生成（0.9.6 前置模块）。

对采集帧生成主体遮罩（白=主体, 黑=背景），用于:
  1. SfM 阶段 masked 图像（提升主体位姿质量）;
  2. 0.9.6 训练侧遮罩损失与训练后主体高斯投影过滤。

依赖 rembg(U2Net, CPU 可用) —— 可选依赖: 未安装时本模块优雅跳过,
由调用方决定是否提示安装（pip install "rembg[cpu]"）。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, List, Optional

from app.utils.logger import get_logger

log = get_logger("MASK")

ProgressCb = Callable[[int, int, str], None]


def rembg_available() -> bool:
    try:
        import rembg  # noqa: F401
        return True
    except ImportError:
        return False


def install_hint() -> str:
    return ('pip install "rembg[cpu]" — 首次运行会自动下载 u2net 模型(约 170MB)')


class SubjectMasker:
    """批量主体遮罩生成器。"""

    def __init__(self, log_cb: Optional[Callable[[str], None]] = None,
                 stop_event: Optional[threading.Event] = None):
        self.log_cb = log_cb
        self.stop_event = stop_event or threading.Event()

    def _emit(self, msg: str) -> None:
        if self.log_cb:
            self.log_cb(msg)

    # ================================================================== #
    def generate(self, frames: List[Path], masks_dir: Path,
                 progress_cb: Optional[ProgressCb] = None) -> Optional[List[Path]]:
        """为每帧生成同名遮罩到 masks_dir。返回遮罩路径列表; 不可用返回 None。"""
        if not frames:
            return []
        if not rembg_available():
            self._emit("rembg 未安装, 主体遮罩跳过。" + install_hint())
            return None
        from rembg import remove, new_session   # 延迟导入: 未装时不拖垮主流程

        masks_dir = Path(masks_dir)
        masks_dir.mkdir(parents=True, exist_ok=True)
        session = new_session("u2net")          # 模型下载一次后复用
        import cv2

        out: List[Path] = []
        total = len(frames)
        for i, f in enumerate(frames):
            if self.stop_event.is_set():
                raise InterruptedError("用户取消遮罩生成")
            img = cv2.imread(str(f), cv2.IMREAD_COLOR)
            if img is None:
                continue
            rgba = remove(img, session=session)
            alpha = rgba[:, :, 3]               # 白=主体, 黑=背景
            mpath = masks_dir / f"mask_{f.stem}.png"
            cv2.imwrite(str(mpath), alpha)
            out.append(mpath)
            if progress_cb:
                progress_cb(i + 1, total, f"遮罩 {f.name}")
        log.info("主体遮罩完成: %d/%d", len(out), total)
        return out
