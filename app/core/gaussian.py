"""3D Gaussian Splatting 训练封装。

将官方 gaussian-splatting (graphdeco-inria) 仓库作为子进程驱动：
    python train.py -s <dataset> -m <model> --iterations N
训练环境（torch+CUDA）与 GUI 壳隔离（见 models/settings.gs_python）。
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional

from app.utils.logger import get_logger

log = get_logger("3DGS")

ProgressCb = Callable[[int, int, str], None]   # (iter, total, msg)

# tqdm 风格进度行: "1200/30000" 或 "[ITER 12000]"
_ITER_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def _free_tcp_port() -> int:
    """取一个当前空闲的 TCP 端口: train.py 的 GUI 监听用, 防并行端口冲突。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


class GaussianTrainError(RuntimeError):
    pass


class GaussianEngine:
    def __init__(self, gs_repo: Path, python_exe: Optional[str] = None,
                 progress_cb: Optional[ProgressCb] = None,
                 stop_event: Optional[threading.Event] = None,
                 cuda_accel: bool = False):
        self.repo = Path(gs_repo)
        self.python = self._resolve_python(python_exe)
        self.progress_cb = progress_cb
        self.stop_event = stop_event or threading.Event()
        self.cuda_accel = cuda_accel

    # ------------------------------------------------------------------ #
    def _resolve_python(self, python_exe: Optional[str]) -> str:
        """训练解释器解析: 显式路径 → 仓库 .venv → 源码模式当前解释器。

        冻结模式下 sys.executable 是主程序 EXE 而非 Python 解释器(B-01):
        若不拦截, train.py 及其参数会被 EXE 自身的 argparse 当非法参数吞掉,
        训练必失败且报错误导用户。缺失时必须报错并附训练环境创建命令。
        """
        if python_exe:
            if Path(python_exe).exists():
                return str(python_exe)
            raise GaussianTrainError(f"训练解释器不可用: {python_exe} 不存在")
        venv_py = self.repo / ".venv" / "Scripts" / "python.exe"
        if venv_py.exists():
            return str(venv_py)
        if getattr(sys, "frozen", False):
            from app.models.settings import PipelineSettings
            from app.utils.deps import training_setup_text
            raise GaussianTrainError(
                "训练解释器不可用: 冻结模式需先创建训练 venv（torch+CUDA 体积大, "
                "不随安装包分发）。创建命令:\n"
                + training_setup_text(PipelineSettings.load()))
        return sys.executable

    # ------------------------------------------------------------------ #
    def train(self, dataset_dir: Path, model_dir: Path,
              iterations: int = 30_000) -> Path:
        train_py = self.repo / "train.py"
        if not train_py.exists():
            raise GaussianTrainError(
                f"未找到 {train_py}。请先克隆官方仓库:\n"
                f"  git clone https://github.com/graphdeco-inria/gaussian-splatting "
                f"--recursive {self.repo}\n"
                f"并在其中创建训练环境（README「训练环境」一节）")
        model_dir.mkdir(parents=True, exist_ok=True)
        # 子进程 cwd=仓库目录, 数据集/模型路径必须转绝对路径, 否则 train.py
        # 在仓库下找不到 sparse/ 而报 "Could not recognize scene type!"。
        dataset_dir = Path(dataset_dir).resolve()
        model_dir = Path(model_dir).resolve()
        cmd = [
            self.python, str(train_py),
            "-s", str(dataset_dir),
            "-m", str(model_dir),
            "--iterations", str(iterations),
            # train.py 无头启动仍会绑定 GUI 监听端口(默认 6006 固定值):
            # 并行两个重建 / 残留训练进程时会 WinError 10048 端口冲突直接崩。
            # 每次分配随机空闲端口根治。
            "--port", str(_free_tcp_port()),
        ]
        # 渲染效率(实测 2500 迭代 +8.7%): data_device=cuda 将图像常驻显存,
        # 免去每次迭代的 H2D 拷贝。帧数过多时显存压力大(约 4MB/帧@1280x720),
        # 超过 250 帧自动回退默认 cpu, 防止 OOM。
        if self.cuda_accel:
            imgs = dataset_dir / "images"
            n = sum(1 for p in imgs.iterdir() if p.is_file()) \
                if imgs.is_dir() else 0
            if 0 < n <= 250:
                cmd += ["--data_device", "cuda"]
                log.info("CUDA 加速: data_device=cuda (帧数 %d)", n)
        env = os.environ.copy()
        env.setdefault("CUDA_VISIBLE_DEVICES", "0")  # 多卡机器默认用 0 号
        log.info("3DGS 训练启动: %s 迭代 %d", model_dir.name, iterations)

        proc = subprocess.Popen(
            cmd, cwd=str(self.repo), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        last_report = 0.0
        tail: List[str] = []          # 输出尾部快照: 失败时随异常带出, 便于定位
        try:
            for line in proc.stdout or []:
                text = line.rstrip()
                if text:
                    log.info("gs| %s", text[:300])
                    tail.append(text)
                    if len(tail) > 15:
                        tail.pop(0)
                if self.stop_event.is_set():
                    proc.kill()
                    raise GaussianTrainError("用户取消")
                match = _ITER_RE.search(text)
                if match and self.progress_cb:
                    cur, total = int(match.group(1)), int(match.group(2))
                    if 0 < cur <= total <= 10_000_000 and cur >= last_report:
                        last_report = cur
                        self.progress_cb(cur, total, "高斯训练")
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
        if code != 0:
            raise GaussianTrainError(
                f"训练进程退出码 {code}。常见原因: 训练环境缺依赖 / 显存不足 "
                f"(尝试 --iterations 减半或降低分辨率重抽帧)"
                + (f"\n—— 训练输出尾部({len(tail)} 行) ——\n" + "\n".join(tail)
                   if tail else ""))

        ply = self.latest_point_cloud(model_dir, iterations)
        if not ply:
            raise GaussianTrainError(f"未找到输出点云: {model_dir}")
        log.info("训练完成: %s", ply)
        return ply

    # ------------------------------------------------------------------ #
    @staticmethod
    def latest_point_cloud(model_dir: Path,
                           expected_iter: Optional[int] = None) -> Optional[Path]:
        root = model_dir / "point_cloud"
        if not root.exists():
            return None
        iters = []
        for d in root.iterdir():
            if d.is_dir() and (d / "point_cloud.ply").exists():
                try:
                    iters.append(int(d.name.replace("iteration_", "")))
                except ValueError:
                    continue
        if not iters:
            return None
        best = expected_iter if expected_iter in iters else max(iters)
        return root / f"iteration_{best}" / "point_cloud.ply"
