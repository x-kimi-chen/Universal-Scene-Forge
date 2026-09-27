# -*- coding: utf-8 -*-
"""渲染效率基准: 3DGS 训练 data_device cpu(默认) vs cuda 对照测试。
同一数据集/迭代数各跑一次, 比较墙钟时间; 差异显著且无 OOM 才建议采纳。"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / "external/gaussian-splatting/.venv/Scripts/python.exe"
TRAIN = ROOT / "external/gaussian-splatting/train.py"
DATA = ROOT / "usf_work/dataset"
ITERS = 2500


def bench(tag: str, extra: list) -> float:
    model = ROOT / f"build/usf_selftest/bench_{tag}"
    if model.exists():
        shutil.rmtree(model)
    cmd = [str(PY), str(TRAIN), "-s", str(DATA), "-m", str(model),
           "--iterations", str(ITERS)] + extra
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(TRAIN.parent), capture_output=True,
                          text=True, errors="replace")
    dt = time.time() - t0
    ok = proc.returncode == 0 and (model / "point_cloud").exists()
    print(f"[{tag}] {'OK ' if ok else 'FAIL'} {dt:.1f}s "
          f"(rc={proc.returncode})", flush=True)
    if not ok:
        print(proc.stdout[-600:], flush=True)
    shutil.rmtree(model, ignore_errors=True)
    return dt if ok else -1.0


t_cpu = bench("cpu", [])
t_cuda = bench("cuda", ["--data_device", "cuda"])
print(f"SUMMARY: cpu={t_cpu:.1f}s cuda={t_cuda:.1f}s", flush=True)
if t_cpu > 0 and t_cuda > 0 and t_cpu > 0:
    gain = (t_cpu - t_cuda) / t_cpu * 100
    print(f"DELTA: {gain:+.1f}%", flush=True)
