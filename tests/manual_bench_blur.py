# -*- coding: utf-8 -*-
"""模糊帧评分的串行 / 并行耗时对比（合成图像，结果可复现）。"""
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

SRC = r"D:/AI_MADE/MADE_one_kaiyuan_Github_Trea/universal_scene_forge"
sys.path.insert(0, SRC)

from app.core.ingest import Ingester  # noqa: E402

N = 60
W, H = 1280, 720


def make_images(root: Path) -> list:
    rng = np.random.default_rng(42)
    paths = []
    for i in range(N):
        img = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
        k = 7 if i % 3 == 0 else 1          # 1/3 为强模糊帧
        if k > 1:
            img = cv2.blur(img, (k, k))
        p = root / f"f{i:04d}.jpg"
        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        paths.append(p)
    return paths


def main() -> int:
    ing = Ingester()
    with tempfile.TemporaryDirectory() as d:
        paths = make_images(Path(d))
        print(f"images={len(paths)} size={W}x{H} files={sum(p.stat().st_size for p in paths)//1024//1024}MB")

        t0 = time.perf_counter()
        seq = [ing._blur_score(p) for p in paths]
        t_seq = time.perf_counter() - t0

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            par = list(pool.map(ing._blur_score, paths))
        t_par = time.perf_counter() - t0

        same = all(abs(a - b) < 1e-9 for a, b in zip(seq, par))
        print(f"serial   {t_seq:.3f}s")
        print(f"parallel {t_par:.3f}s")
        print(f"speedup  {t_seq / t_par:.2f}x  results_identical={same}")

        t0 = time.perf_counter()
        kept = ing.filter_blurry(paths)
        print(f"filter_blurry {time.perf_counter() - t0:.3f}s kept={len(kept)}/{len(paths)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
