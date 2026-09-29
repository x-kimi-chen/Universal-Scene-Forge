# -*- coding: utf-8 -*-
"""训练后主体高斯过滤（0.9.6 M1.5, 免 train.py 补丁方案）。

原理: 把训练得到的 3DGS 点云逐高斯投影到各采集视图, 采样主体遮罩;
仅在「多数可见视图中位于主体内」的高斯保留 —— 从而剔除背景垫子等
非主体高斯, 让网格重建聚焦主体（修复 Q-01 主体缺失）。

依赖: ns_convert 的 COLMAP 二进制解析 + plyfile(训练环境已含)。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from app.utils.logger import get_logger

log = get_logger("GFILTER")


def filter_gaussians_by_masks(ply_in: Path, ply_out: Path,
                              colmap_dir: Path, masks_dir: Path,
                              majority: float = 0.5) -> Optional[Path]:
    """按主体遮罩过滤 3DGS 点云。

    Args:
        ply_in: 训练产出的 point_cloud.ply (3DGS 格式)
        ply_out: 过滤后输出路径 (保留原始全部字段)
        colmap_dir: COLMAP 数据集目录 (sparse/0 的 cameras.bin/images.bin)
        masks_dir: 遮罩目录 (mask_<帧名>.png, 白=主体)
        majority: 可见视图中位于主体内的最小比例

    Returns:
        过滤后的 ply 路径; 无法解析/无遮罩时返回 None (调用方回退原网格)。
    """
    try:
        from plyfile import PlyData
    except ImportError:
        log.warning("plyfile 未安装, 无法过滤")
        return None

    from app.core.ns_convert import _read_cameras_bin, _read_images_bin

    sparse = colmap_dir / "sparse" / "0"
    if not (sparse / "cameras.bin").exists():
        sparse = colmap_dir / "sparse"
    if not (sparse / "cameras.bin").exists():
        log.warning("COLMAP 数据缺失, 跳过高斯过滤")
        return None
    try:
        import cv2
    except ImportError:
        return None

    cams = _read_cameras_bin(sparse / "cameras.bin")
    imgs = _read_images_bin(sparse / "images.bin")

    # 遮罩按帧名索引 (mask_<stem>.png)
    masks = {}
    for mp in Path(masks_dir).glob("mask_*.png"):
        stems = mp.stem[len("mask_"):]
        masks[stems] = mp
    if not masks:
        log.warning("无可用遮罩, 跳过高斯过滤")
        return None

    ply = PlyData.read(str(ply_in))
    vertex = ply["vertex"]
    xyz = np.stack([vertex["x"], vertex["y"], vertex["z"]], axis=1)
    n = len(xyz)

    # 旋转矩阵 + 平移 per image
    from app.core.ns_convert import qvec2rotmat
    votes = np.zeros(n, dtype=np.int32)
    visible = np.zeros(n, dtype=np.int32)

    used = 0
    for img in imgs:
        stem = Path(img["name"]).stem
        mp = masks.get(stem)
        if mp is None:
            continue
        mask = cv2.imread(str(mp), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            continue
        cam = cams[img["camera_id"]]
        params = cam["params"]
        if cam["model_id"] == 1:       # PINHOLE
            fx, fy, cx, cy = params
        elif cam["model_id"] == 0:
            fx = fy = params[0]; cx, cy = params[1], params[2]
        else:
            fx = fy = params[0]; cx, cy = params[2], params[3]
        R = qvec2rotmat(img["q"])
        T = np.array(img["t"])

        pc = xyz @ R.T + T              # 世界 → 相机 (w2c: X' = RX + T)
        z = pc[:, 2]
        front = z > 1e-6
        u = np.zeros(n); vv = np.zeros(n)
        u[front] = fx * pc[front, 0] / z[front] + cx
        vv[front] = fy * pc[front, 1] / z[front] + cy
        h, w = mask.shape[:2]
        inside = front & (u >= 0) & (u < w) & (vv >= 0) & (vv < h)
        ui = u[inside].astype(int); vi = vv[inside].astype(int)
        subject = mask[vi, ui] > 127

        idx_inside = np.where(inside)[0]
        votes[idx_inside[subject]] += 1
        visible[idx_inside] += 1
        used += 1

    if used == 0 or visible.max() == 0:
        log.warning("无有效投影视图, 跳过高斯过滤")
        return None

    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(visible > 0, votes / np.maximum(visible, 1), -1.0)
    keep = (visible > 0) & (ratio >= majority)
    log.info("高斯过滤: %d/%d 保留 (主体内占比 ≥ %.0f%%, 视图 %d)",
             int(keep.sum()), n, majority * 100, used)
    if keep.sum() < 100:
        log.warning("过滤后高斯过少 (%d), 放弃过滤以保底产出", int(keep.sum()))
        return None

    # plyfile 的元素下标会把整数数组当属性名 —— 用 (name, properties, count)
    # 重构子集元素 (numpy 结构化数组切片)
    from plyfile import PlyElement
    kept_data = vertex.data[keep]
    el = PlyElement(vertex.name, vertex.properties, len(kept_data),
                    comments=vertex.comments)
    el.data = kept_data
    out = PlyData([el], text=ply.text)
    out.write(str(ply_out))
    log.info("过滤后点云: %s", ply_out)
    return ply_out
