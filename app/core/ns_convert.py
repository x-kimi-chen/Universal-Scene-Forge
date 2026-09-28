# -*- coding: utf-8 -*-
"""COLMAP 数据集 → nerfstudio transforms.json 转换器（0.9.6 前置）。

解析 COLMAP 二进制 (cameras.bin / images.bin)，生成 nerfstudio-data
格式 transforms.json，使 splatfacto 等基于 nerfstudio-data 的训练器
可直接消费 USF 的 COLMAP 数据集。

坐标转换: COLMAP 为 OpenCV 约定 (+Z 前向, +Y 向下)，nerfstudio 使用
OpenGL 约定 —— c2w 后翻转 Y/Z 轴（与官方 colmap2nerfstudio 一致）。
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

# COLMAP 相机模型 ID → 参数个数
_MODEL_PARAMS = {
    0: 3,   # SIMPLE_PINHOLE: f, cx, cy
    1: 4,   # PINHOLE: fx, fy, cx, cy
    2: 4,   # SIMPLE_RADIAL
    3: 5,   # RADIAL
    4: 8,   # OPENCV
    5: 8,   # OPENCV_FISHEYE
    6: 8,   # FULL_OPENCV
    7: 13,  # FOV
    8: 12,  # SIMPLE_RADIAL_FISHEYE
    9: 12,  # RADIAL_FISHEYE
    10: 12, # THIN_PRISM_FISHEYE
}
_MODEL_NAMES = {0: "SIMPLE_PINHOLE", 1: "PINHOLE", 2: "SIMPLE_RADIAL",
                3: "RADIAL", 4: "OPENCV"}


def _read_cameras_bin(path: Path) -> dict:
    cams = {}
    with open(path, "rb") as f:
        num = struct.unpack("<Q", f.read(8))[0]   # size_t 计数
        for _ in range(num):
            cid, mid = struct.unpack("<ii", f.read(8))
            w, h = struct.unpack("<QQ", f.read(16))
            n = _MODEL_PARAMS.get(mid, 4)
            params = struct.unpack(f"<{n}d", f.read(8 * n))
            name = b""
            while True:
                ch = f.read(1)
                if ch in (b"\x00", b""):
                    break
                name += ch
            cams[cid] = {"model_id": mid, "width": w, "height": h,
                         "params": params, "name": name.decode("utf-8", "replace")}
    return cams


def _read_images_bin(path: Path) -> list:
    imgs = []
    with open(path, "rb") as f:
        num = struct.unpack("<Q", f.read(8))[0]
        for _ in range(num):
            iid = struct.unpack("<i", f.read(4))[0]
            q = struct.unpack("<4d", f.read(32))     # qw qx qy qz
            t = struct.unpack("<3d", f.read(24))
            cid = struct.unpack("<i", f.read(4))[0]
            name = b""
            while True:
                ch = f.read(1)
                if ch in (b"\x00", b""):
                    break
                name += ch
            npts = struct.unpack("<Q", f.read(8))[0]
            f.seek(npts * 24, 1)                     # 跳过二维点
            imgs.append({"id": iid, "q": q, "t": t, "camera_id": cid,
                         "name": name.decode("utf-8", "replace")})
    return imgs


def qvec2rotmat(q):
    w, x, y, z = q
    return np.array([
        [1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w,   2*x*z + 2*y*w],
        [2*x*y + 2*z*w,     1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w],
        [2*x*z - 2*y*w,     2*y*z + 2*x*w,   1 - 2*x*x - 2*y*y]])


def convert_colmap_to_ns(colmap_dir: Path, out_path: Optional[Path] = None) -> Path:
    """COLMAP 数据集 → transforms.json。返回输出路径。"""
    colmap_dir = Path(colmap_dir)
    sparse = colmap_dir / "sparse" / "0"
    if not (sparse / "cameras.bin").exists():
        sparse = colmap_dir / "sparse"
    cams = _read_cameras_bin(sparse / "cameras.bin")
    imgs = _read_images_bin(sparse / "images.bin")

    frames = []
    for img in imgs:
        cam = cams[img["camera_id"]]
        p = cam["params"]
        if cam["model_id"] == 1:       # PINHOLE: fx fy cx cy
            fx, fy, cx, cy = p
        elif cam["model_id"] == 0:     # SIMPLE_PINHOLE: f cx cy
            fx = fy = p[0]; cx, cy = p[1], p[2]
        else:                          # 其他模型近似取前两个焦距参数
            fx = fy = p[0]; cx, cy = p[2], p[3]
        cam_w, cam_h = cam["width"], cam["height"]
        R = qvec2rotmat(img["q"])
        T = np.array(img["t"])
        c2w = np.eye(4)
        c2w[:3, :3] = R
        c2w[:3, 3] = T
        c2w = np.linalg.inv(c2w)       # w2c → c2w (camera-to-world)
        c2w[0:3, 1:3] *= -1            # OpenCV → OpenGL (nerfstudio 约定)
        frames.append({
            "file_path": f"images/{img['name']}",
            "transform_matrix": c2w.tolist(),
            "fl_x": fx, "fl_y": fy, "cx": cx, "cy": cy,
            "w": cam_w, "h": cam_h,
        })
    out = Path(out_path) if out_path else colmap_dir / "transforms.json"
    fx_last = frames[-1]["fl_x"] if frames else 1.0
    w_last = frames[-1]["w"] if frames else 1.0
    data = {
        "camera_angle_x": float(2 * np.arctan(w_last / (2 * fx_last))),
        "frames": frames,
    }
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return out
