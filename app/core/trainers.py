# -*- coding: utf-8 -*-
"""训练器抽象层（0.9.6 前置模块）。

目标: 流水线其余阶段不感知训练引擎差异 —— 通过 TrainingEngine 协议 +
注册表按 settings.trainer 选择引擎。0.9.5 的 GaussianEngine(官方
gaussian-splatting) 保持零改动, 由 Builtin3dgsAdapter 委托。

适配器状态:
- builtin  : ✅ 完整可用 (0.9.5 现状)
- opensplat: 骨架 (检测 + 命令构建; 需下载对应 CUDA 构建二进制后实机联调)
- nerfstudio: ✅ 环境已安装 (D:/nerfstudio, torch 2.9.0+cu128) 且
  splatfacto 训练实测通过 (checkpoint 已产出)
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from app.utils.logger import get_logger

log = get_logger("TRAINER")

ProgressCb = Callable[[int, int, str], None]   # (cur, total, msg)


class TrainingEngineError(RuntimeError):
    pass


@dataclass
class EngineInfo:
    key: str
    name: str
    available: bool
    detail: str = ""
    accelerate: str = "cuda"     # cuda | cpu | cuda+cpu


class TrainingEngine(Protocol):
    """训练引擎协议: 与 GaussianEngine.train 对齐的最小接口。"""

    def train(self, dataset_dir: Path, model_dir: Path,
              iterations: int) -> Path:    # 返回 point cloud 路径
        ...


# --------------------------------------------------------------------- #
# 内置 3DGS (gaussian-splatting) —— 委托现有实现, 零行为变化
# --------------------------------------------------------------------- #
class Builtin3dgsAdapter:
    name = "gaussian-splatting (内置)"

    def __init__(self, settings, progress_cb=None, stop_event=None):
        from app.core.gaussian import GaussianEngine   # 延迟: 避免循环导入
        python_exe = None
        from app.utils import deps
        py = deps.training_python(settings)
        python_exe = str(py) if py else None
        self._engine = GaussianEngine(
            gs_repo=Path(settings.gs_repo), python_exe=python_exe,
            progress_cb=progress_cb, stop_event=stop_event,
            cuda_accel=getattr(settings, "enable_cuda_accel", True))

    @staticmethod
    def detect(settings) -> EngineInfo:
        from app.utils import deps
        ok = deps.training_python(settings) is not None \
            and (Path(settings.gs_repo) / "train.py").exists()
        return EngineInfo("builtin", "gaussian-splatting (内置)", ok,
                          "训练 venv + 仓库就绪" if ok else "需创建训练环境")

    def train(self, dataset_dir: Path, model_dir: Path,
              iterations: int) -> Path:
        return self._engine.train(dataset_dir, model_dir, iterations)


# --------------------------------------------------------------------- #
# OpenSplat —— 单二进制开源引擎 (骨架: 检测 + 命令构建)
# --------------------------------------------------------------------- #
class OpenSplatAdapter:
    name = "OpenSplat"

    def __init__(self, exe: str, progress_cb=None, stop_event=None):
        self.exe = exe
        self.progress_cb = progress_cb
        self.stop_event = stop_event

    @staticmethod
    def detect(settings) -> EngineInfo:
        # 官方 Releases 无 Windows 预编译版 (已实测扫描), 约定检测:
        # settings.opensplat_exe → D:/OpenSplat/opensplat.exe (用户指定目录) → PATH
        cands = []
        exe_setting = getattr(settings, "opensplat_exe", None)
        if exe_setting:
            cands.append(Path(exe_setting))
        cands.append(Path("D:/OpenSplat/opensplat.exe"))
        w = shutil.which("opensplat")
        if w:
            cands.append(Path(w))
        for cand in cands:
            if cand.exists():
                return EngineInfo("opensplat", "OpenSplat", True, str(cand))
        return EngineInfo("opensplat", "OpenSplat", False,
                          "未找到 opensplat.exe (官方无 Windows 预编译版; "
                          "自行编译后放置 D:/OpenSplat/, 详见该目录 README.txt)")

    def train(self, dataset_dir: Path, model_dir: Path,
              iterations: int) -> Path:
        """OpenSplat CLI: opensplat <input> -n <iters> -o <output>。

        输入需 COLMAP 结构 (与内置引擎一致, 复用同一 dataset)。
        骨架状态: 需在装有 OpenSplat 的机器上完成参数与产物路径联调。
        """
        import subprocess
        import sys
        from app.core.gaussian import GaussianTrainError
        model_dir = Path(model_dir).resolve()
        model_dir.mkdir(parents=True, exist_ok=True)
        out_ply = model_dir / "opensplat.ply"
        cmd = [self.exe, str(Path(dataset_dir).resolve()),
               "-n", str(iterations), "-o", str(out_ply)]
        log.info("OpenSplat 训练启动: %s", " ".join(cmd))
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace", creationflags=creation)
        for line in proc.stdout or []:
            log.info("opensplat| %s", line.rstrip()[:300])
        if proc.wait() != 0 or not out_ply.exists():
            raise TrainingEngineError(
                "OpenSplat 训练失败 (骨架状态: 需实机联调参数与产物路径)")
        return out_ply


# --------------------------------------------------------------------- #
# nerfstudio splatfacto —— 骨架 (独立环境, 需用户确认后安装)
# --------------------------------------------------------------------- #
NS_ROOT = Path("D:/nerfstudio")   # 用户指定安装目录


class NerfstudioAdapter:
    name = "nerfstudio splatfacto"

    def __init__(self, ns_exe: str, progress_cb=None, stop_event=None):
        self.ns_exe = ns_exe
        self.progress_cb = progress_cb
        self.stop_event = stop_event

    @staticmethod
    def _ns_train_exe() -> Path:
        return NS_ROOT / ".venv" / "Scripts" / "ns-train.exe"

    @staticmethod
    def detect(settings) -> EngineInfo:
        exe = NerfstudioAdapter._ns_train_exe()
        ok = exe.exists()
        return EngineInfo("nerfstudio", "nerfstudio splatfacto", ok,
                          str(exe) if ok else
                          "未找到 ns-train (安装于 D:/nerfstudio)")

    def train(self, dataset_dir: Path, model_dir: Path,
              iterations: int) -> Path:
        """已验证的 splatfacto 训练配方 (0.9.5 实测通过):

        1. app/core/ns_convert.py 将 COLMAP 数据集转为 transforms.json;
        2. 经 vcvars64 + 环境变量 (MAX_JOBS=1 / NVCC_APPEND_FLAGS=
           "-Xcompiler /Zc:preprocessor" / CCCL_IGNORE...) 启动 ns-train
           splatfacto --pipeline.datamanager.dataparser nerfstudio-data;
        3. 训练完成产出 checkpoint (nerfstudio_models/step-*.ckpt),
           由 ns-export gaussian-splat 转出 PLY (0.9.6 接入导出链)。

        注意: --vis viewer 会阻塞进程; 无头运行需控制收尾。
        """
        from app.core.ns_convert import convert_colmap_to_ns
        ns_data = convert_colmap_to_ns(Path(dataset_dir))
        raise TrainingEngineError(
            "nerfstudio 训练需经 vcvars64 包装脚本启动 (gsplat JIT 编译"
            "要求 MSVC 环境与 MAX_JOBS=1), 完整自动化接入在 0.9.6 完成。"
            f"已转换数据: {ns_data}")


# --------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------- #
class PostshotAdapter:
    """Jawset Postshot CLI 适配骨架 (专有软件, 需用户自装并授权)。

    Postshot 提供 postshot-cli (train/render 导出), 安装后检测路径即自动
    接入; 未安装时优雅跳过。CLI 参数以安装版本帮助为准 —— 实机联调入
    后续版本 (用户环境就绪后)。
    """

    COMMON_PATHS = [
        Path("C:/Program Files/Jawset/Postshot/bin/postshot-cli.exe"),
        Path("D:/Program Files/Jawset/Postshot/bin/postshot-cli.exe"),
    ]

    def __init__(self, exe: str, progress_cb=None, stop_event=None):
        self.exe = exe
        self.progress_cb = progress_cb
        self.stop_event = stop_event

    @staticmethod
    def detect(settings) -> EngineInfo:
        cands = []
        exe_setting = getattr(settings, "postshot_cli", None)
        if exe_setting:
            cands.append(Path(exe_setting))
        cands.extend(PostshotAdapter.COMMON_PATHS)
        w = shutil.which("postshot-cli")
        if w:
            cands.append(Path(w))
        for cand in cands:
            if Path(cand).exists():
                return EngineInfo("postshot", "Postshot (Jawset)", True, str(cand))
        return EngineInfo("postshot", "Postshot (Jawset)", False,
                          "未找到 postshot-cli (安装 Postshot 后自动接入)")

    def train(self, dataset_dir: Path, model_dir: Path,
              iterations: int) -> Path:
        raise TrainingEngineError(
            "Postshot 适配为骨架状态: 需在装有 Postshot 的机器上按其 "
            "postshot-cli 文档联调训练命令 (后续版本完成)。")


_ENGINES = {
    "builtin": Builtin3dgsAdapter,
    "opensplat": OpenSplatAdapter,
    "nerfstudio": NerfstudioAdapter,
    "postshot": PostshotAdapter,
}


def available_engines(settings) -> dict:
    """返回 {key: EngineInfo} 全量探测结果。"""
    out = {}
    for key, cls in _ENGINES.items():
        try:
            out[key] = cls.detect(settings)
        except Exception as exc:  # noqa: BLE001 探测失败不拖垮
            log.warning("引擎探测失败 %s: %s", key, exc)
            out[key] = EngineInfo(key, key, False, f"探测失败: {exc}")
    return out


def create_engine(settings, progress_cb=None, stop_event=None):
    """按 settings.trainer 创建引擎; 所选引擎不可用时回退内置并告警。"""
    key = getattr(settings, "trainer", "builtin")
    cls = _ENGINES.get(key, Builtin3dgsAdapter)
    info = cls.detect(settings)
    if not info.available and key != "builtin":
        log.warning("训练器 %s 不可用 (%s), 回退内置 gaussian-splatting",
                    key, info.detail)
        cls = Builtin3dgsAdapter
    return cls(settings, progress_cb=progress_cb, stop_event=stop_event)
