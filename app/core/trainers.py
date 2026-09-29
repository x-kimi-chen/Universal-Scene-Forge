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
import sys
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

    def __init__(self, ns_exe: str, timeout_s: int = 14_400,
                 progress_cb=None, stop_event=None, log_cb=None):
        self.ns_exe = ns_exe
        self.timeout_s = timeout_s
        self.progress_cb = progress_cb
        self.stop_event = stop_event or __import__("threading").Event()
        self.log_cb = log_cb

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
        """全自动 splatfacto 训练 (配方已在 0.9.5 实测通过)。

        流程: COLMAP → transforms.json 转换 → 生成 vcvars 包装脚本
        (MAX_JOBS=1 / NVCC_APPEND_FLAGS / CCCL_IGNORE) → ns-train 轮询
        checkpoint → 训练完成自动收尾 → ns-export 转出 PLY。
        """
        import subprocess
        import time

        from app.core.ns_convert import convert_colmap_to_ns
        if not self._ns_train_exe().exists():
            raise TrainingEngineError(
                f"nerfstudio 未安装: {self._ns_train_exe()} 不存在")
        dataset_dir = Path(dataset_dir).resolve()
        ns_data_dir = convert_colmap_to_ns(dataset_dir)
        data_name = dataset_dir.name or "usf_data"

        venv_scripts = (NS_ROOT / ".venv" / "Scripts").resolve()
        out_dir = (NS_ROOT / "usf_runs").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = f"usf{int(time.time())}"
        run_dir = out_dir / data_name / "splatfacto" / ts
        config_yml = run_dir / "config.yml"
        models_dir = run_dir / "nerfstudio_models"
        target_step = max(iterations - 1, 1)

        bat = out_dir / f"usf_ns_train_{ts}.bat"
        bat.write_text("\n".join([
            "@echo off",
            'call "C:\\Program Files (x86)\\Microsoft Visual Studio\\2022'
            '\\BuildTools\\VC\\Auxiliary\\Build\\vcvars64.bat" >nul',
            f"set PATH={venv_scripts.as_posix()};%PATH%",
            "set MAX_JOBS=1",
            'set "CCCL_IGNORE_MSVC_TRADITIONAL_PREPROCESSOR_WARNING=1"',
            'set "NVCC_APPEND_FLAGS=-Xcompiler /Zc:preprocessor"',
            f"{self._ns_train_exe().as_posix()} splatfacto "
            f"--data {ns_data_dir.parent.as_posix()} "
            f"--max-num-iterations {iterations} "
            f"--output-dir {out_dir.as_posix()} --timestamp {ts}",
        ]) + "\n", encoding="utf-8")
        log.info("nerfstudio 训练启动: %s 迭代 %d", bat, iterations)

        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(["cmd", "/c", str(bat)],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace", creationflags=creation)
        ckpt = models_dir / f"step-{target_step:09d}.ckpt"
        deadline = time.time() + self.timeout_s
        try:
            for line in proc.stdout or []:
                text = line.rstrip()
                if self.log_cb:
                    self.log_cb(text[:300])
                if ckpt.exists():
                    time.sleep(5)          # 等待 checkpoint 写盘完成
                    proc.kill()            # 训练完成, viewer 会阻塞进程
                    break
                if self.stop_event.is_set() or time.time() > deadline:
                    proc.kill()
                    raise TrainingEngineError("nerfstudio 训练被取消或超时")
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()

        if not ckpt.exists():
            raise TrainingEngineError(
                f"nerfstudio 训练未产出 checkpoint ({ckpt}), "
                "详见上方引擎输出")

        # ns-export: checkpoint → PLY (供网格重建阶段使用)
        ply_out = Path(model_dir).resolve() / "nerfstudio_splat.ply"
        ply_out.parent.mkdir(parents=True, exist_ok=True)
        # ns-export 经包装脚本执行: torch 2.6+ weights_only 默认拒绝
        # checkpoint 中的 numpy 对象, 包装器恢复默认 (可信来源=自产 checkpoint)
        wrapper = NS_ROOT / "ns_export_safe.py"
        log.info("导出 PLY: %s", ply_out)
        exp = subprocess.run(
            [str(venv_scripts / "python.exe"), str(wrapper),
             "gaussian-splat", "--load-config", str(config_yml),
             "--output-dir", str(Path(model_dir).resolve())],
            capture_output=True, text=True, errors="replace",
            creationflags=creation, timeout=self.timeout_s)
        ply = ply_out if ply_out.exists() else model_dir / "splat.ply"
        if not Path(ply).exists():
            raise TrainingEngineError(
                f"ns-export 未产出 PLY (退出码 {exp.returncode})")
        log.info("nerfstudio 训练完成: %s", ply)
        return Path(ply)


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


def create_engine(settings, progress_cb=None, stop_event=None,
                  log_cb=None):
    """按 settings.trainer 创建引擎; 所选引擎不可用时回退内置并告警。"""
    key = getattr(settings, "trainer", "builtin")
    cls = _ENGINES.get(key, Builtin3dgsAdapter)
    info = cls.detect(settings)
    if not info.available and key != "builtin":
        log.warning("训练器 %s 不可用 (%s), 回退内置 gaussian-splatting",
                    key, info.detail)
        cls = Builtin3dgsAdapter
    import inspect
    sig_params = inspect.signature(cls.__init__).parameters
    kwargs = {"progress_cb": progress_cb, "stop_event": stop_event}
    if "log_cb" in sig_params:
        kwargs["log_cb"] = log_cb
    if "settings" in sig_params:
        return cls(settings, **kwargs)
    if "ns_exe" in sig_params:
        return cls(str(getattr(settings, "nerfstudio_root",
                              NS_ROOT / ".venv" / "Scripts" / "ns-train.exe")),
                   **kwargs)
    return cls(**kwargs)
