"""COLMAP SfM 位姿求解封装。

输出标准 3DGS 数据集布局:
    dataset/images/          原始帧
    dataset/sparse/0/        cameras.bin / images.bin / points3D.bin
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional

from app.utils.logger import get_logger

log = get_logger("SFM")

ProgressCb = Callable[[int, int, str], None]


class SfmError(RuntimeError):
    pass


class SfmRunner:
    def __init__(self, colmap_exe: Optional[str] = None,
                 progress_cb: Optional[ProgressCb] = None,
                 stop_event: Optional[threading.Event] = None,
                 use_gpu: bool = True,
                 tools_root: Optional[str] = None):
        # 三级查找(P1): settings 显式路径 → PATH → tools_root 便携版,
        # 与 deps.check_tools / Ingester(FFmpeg) 完全同口径 —— 否则体检
        # 显示 [OK] 而流水线实际报「COLMAP 未安装」。
        self.colmap = colmap_exe or shutil.which("colmap")
        if not self.colmap and tools_root:
            from app.dcc.auto_installer import find_portable_exe
            exe = find_portable_exe("colmap", Path(tools_root))
            self.colmap = str(exe) if exe else None
        self._major_version_cache: Optional[int] = None
        self.progress_cb = progress_cb
        self.stop_event = stop_event or threading.Event()
        self.use_gpu = use_gpu

    def _major_version(self) -> int:
        """COLMAP 主版本号 (4.x 重构了 GPU 选项命名, 需适配)。"""
        if self._major_version_cache is None:
            major = 3
            try:
                out = subprocess.run(
                    [self.colmap, "-h"], capture_output=True, text=True,
                    timeout=30, errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if sys.platform == "win32" else 0)
                m = re.search(r"COLMAP\s+(\d+)\.(\d+)", out.stdout)
                if m:
                    major = int(m.group(1))
            except Exception:  # noqa: BLE001
                pass
            self._major_version_cache = major
        return self._major_version_cache

    def _gpu_flags(self) -> dict:
        """按版本返回特征提取/匹配的 GPU 开关参数。"""
        if self._major_version() >= 4:
            return {"fe": "--FeatureExtraction.use_gpu",
                    "fm": "--FeatureMatching.use_gpu"}
        return {"fe": "--SiftExtraction.use_gpu",
                "fm": "--SiftMatching.use_gpu"}

    # ------------------------------------------------------------------ #
    def build_dataset(self, frames: List[Path], work_dir: Path) -> Path:
        """组织 COLMAP 数据集目录并执行三步 SfM。"""
        if not self.colmap:
            raise SfmError("COLMAP 未安装（依赖体检中可一键安装便携版）")
        dataset = work_dir / "dataset"
        images_dir = dataset / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        for f in frames:  # 平坦拷贝，规避中文/空格路径问题
            dst = images_dir / f.name
            if not dst.exists():
                dst.write_bytes(f.read_bytes())

        db = dataset / "database.db"
        sparse = dataset / "sparse" / "0"
        sparse.mkdir(parents=True, exist_ok=True)

        steps = [
            ("特征提取", [
                "feature_extractor",
                "--database_path", str(db), "--image_path", str(images_dir),
                "--ImageReader.single_camera", "1",
                # PINHOLE(P2): COLMAP 默认输出 SIMPLE_RADIAL, 而
                # gaussian-splatting 仅接受 PINHOLE/SIMPLE_PINHOLE,
                # 否则训练阶段必然报 "Colmap camera model not handled"。
                "--ImageReader.camera_model", "PINHOLE",
                self._gpu_flags()["fe"], str(int(self.use_gpu)),
            ]),
            ("特征匹配", [
                "exhaustive_matcher",
                "--database_path", str(db),
                self._gpu_flags()["fm"], str(int(self.use_gpu)),
            ]),
            ("增量式重建", [
                "mapper",
                "--database_path", str(db), "--image_path", str(images_dir),
                "--output_path", str(dataset / "sparse"),
            ]),
        ]
        total = len(steps)
        for i, (name, args) in enumerate(steps):
            if self.stop_event.is_set():
                raise SfmError("用户取消")
            self._report(i, total, name)
            cmd = [self.colmap] + args
            log.info("COLMAP %s ...", name)
            self._run(cmd)
        self._report(total, total, "SfM 完成")

        # mapper 输出目录为 sparse/0（单模型）
        if not (sparse / "cameras.bin").exists():
            # 容错：有的版本直接写 sparse/
            alt = dataset / "sparse"
            if (alt / "cameras.bin").exists():
                (alt / "cameras.bin").rename(sparse / "cameras.bin")
                (alt / "images.bin").rename(sparse / "images.bin")
                (alt / "points3D.bin").rename(sparse / "points3D.bin")
            else:
                raise SfmError(
                    "COLMAP 未能求解相机位姿（画面纹理弱/运动不足）。"
                    "建议提高抽帧密度或增大视角变化")
        return dataset

    # ------------------------------------------------------------------ #
    def _run(self, cmd: List[str]) -> None:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        tail: List[str] = []   # 输出尾部: 失败时随异常带出, 否则用户只看到退出码
        for line in proc.stdout or []:
            text = line.rstrip()
            if text:
                log.info("colmap| %s", text[:300])
                tail.append(text)
                if len(tail) > 15:
                    tail.pop(0)
            if self.stop_event.is_set():
                proc.kill()
                raise SfmError("用户取消")
        code = proc.wait()
        if code != 0:
            detail = "\n".join(tail[-8:]) if tail else "（无输出）"
            raise SfmError(f"COLMAP 退出码 {code}: {' '.join(cmd[:3])} ...\n"
                           f"—— COLMAP 输出尾部 ——\n{detail}")

    def _report(self, done: int, total: int, msg: str) -> None:
        if self.progress_cb:
            self.progress_cb(done, total, msg)
