# -*- coding: utf-8 -*-
"""3DGS 深度渲染桥接（USF 自有代码, MIT）。

在用户本地 gaussian-splatting 仓库 + 训练 venv 下运行，为 TSDF 融合输出:
    depth/NNNNN.npy   浮点深度 (z-depth, 场景单位)
    color/NNNNN.png   该视角的 3DGS 渲染图
    views.json        每帧内参/外参/尺寸

深度 = alpha 累积 / 逆深度累积。3DGS 光栅化只返回 sum(1/z * alpha * T)，
直接取倒数会因半透明层叠加而系统性偏远；用白色覆盖 + 黑背景再渲染一次
得到 alpha 累积，二者相除即为 alpha 加权深度。

该脚本不复制、不打包 Inria 源码；运行时依赖用户自行获取的仓库。
"""
from __future__ import annotations

import json
import os
import sys
from argparse import ArgumentParser
from pathlib import Path

# 以绝对路径启动时 sys.path[0] 是脚本目录; Inria 模块在仓库根（cwd）
if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd())

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from tqdm import tqdm  # noqa: E402

from arguments import ModelParams, PipelineParams, get_combined_args  # noqa: E402
from gaussian_renderer import GaussianModel, render  # noqa: E402
from scene import Scene  # noqa: E402
from utils.general_utils import safe_state  # noqa: E402
from utils.graphics_utils import fov2focal  # noqa: E402

try:
    from diff_gaussian_rasterization import SparseGaussianAdam
    SPARSE_ADAM_AVAILABLE = True
except Exception:  # noqa: BLE001
    SPARSE_ADAM_AVAILABLE = False


def main() -> int:
    parser = ArgumentParser(description="USF 3DGS depth renderer")
    model = ModelParams(parser, sentinel=True)
    pipeline = PipelineParams(parser)
    parser.add_argument("--iteration", default=-1, type=int)
    parser.add_argument("--out_dir", required=True)
    parser.add_argument("--scale", default=0.5, type=float)
    parser.add_argument("--depth_trunc", default=30.0, type=float)
    parser.add_argument("--quiet", action="store_true")
    args = get_combined_args(parser)

    out_dir = Path(args.out_dir)
    (out_dir / "depth").mkdir(parents=True, exist_ok=True)
    (out_dir / "color").mkdir(parents=True, exist_ok=True)
    safe_state(args.quiet)
    dataset = model.extract(args)
    pipe = pipeline.extract(args)

    with torch.no_grad():
        gaussians = GaussianModel(dataset.sh_degree)
        scene = Scene(dataset, gaussians, load_iteration=args.iteration,
                      shuffle=False)
        bg_color = [1, 1, 1] if dataset.white_background else [0, 0, 0]
        background = torch.tensor(bg_color, dtype=torch.float32, device="cuda")
        views = scene.getTrainCameras()
        print(f"views={len(views)} scale={args.scale}", flush=True)

        meta = []
        for idx, view in enumerate(tqdm(views, desc="depth")):
            out = render(view, gaussians, pipe, background,
                         separate_sh=SPARSE_ADAM_AVAILABLE)
            rgb = out["render"].clamp(0, 1).permute(1, 2, 0).cpu().numpy()
            inv = out["depth"][0].cpu().numpy().astype(np.float32)
            white = torch.ones_like(out["render"])
            bg_black = torch.zeros(3, dtype=torch.float32, device="cuda")
            alpha_out = render(view, gaussians, pipe, bg_black,
                               override_color=white,
                               separate_sh=SPARSE_ADAM_AVAILABLE)
            alpha = alpha_out["render"][0].clamp(0, 1).cpu().numpy()
            depth = np.zeros_like(inv)
            valid = (inv > 1e-4) & (alpha > 1e-3)
            depth[valid] = alpha[valid] / inv[valid]
            depth[depth > args.depth_trunc] = 0.0
            depth = cv2.medianBlur(depth, 3)
            h, w = depth.shape
            sz = (max(2, int(w * args.scale)), max(2, int(h * args.scale)))
            depth_s = cv2.resize(depth, sz, interpolation=cv2.INTER_AREA)
            rgb_s = cv2.resize(rgb, sz, interpolation=cv2.INTER_AREA)
            np.save(out_dir / "depth" / f"{idx:05d}.npy",
                    depth_s.astype(np.float16))
            cv2.imwrite(str(out_dir / "color" / f"{idx:05d}.png"),
                        cv2.cvtColor((rgb_s * 255).astype(np.uint8),
                                     cv2.COLOR_RGB2BGR))
            fx = fov2focal(view.FoVx, view.image_width) * args.scale
            fy = fov2focal(view.FoVy, view.image_height) * args.scale
            R = np.asarray(view.R, dtype=np.float64)
            center = view.camera_center.detach().cpu().numpy().astype(np.float64)
            w2c = np.eye(4)
            w2c[:3, :3] = R.T
            w2c[:3, 3] = -R.T @ center
            meta.append({
                "index": idx,
                "image_name": view.image_name,
                "width": sz[0], "height": sz[1],
                "fx": float(fx), "fy": float(fy),
                "cx": sz[0] / 2.0, "cy": sz[1] / 2.0,
                "w2c": w2c.tolist(),
            })
        (out_dir / "views.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"DONE {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 —— 必须把异常写进 stdout
        print(f"[USF-DEPTH] FATAL {exc}", flush=True)
        sys.exit(1)
