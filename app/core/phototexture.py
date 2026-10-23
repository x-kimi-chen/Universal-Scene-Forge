"""多视角影像投影贴色（USF 自研, MIT）。

3DGS 的 f_dc 只代表球谐直流项，黑色高光物体的外观主要落在高阶 SH 上：
直接用它做顶点色会把黑色鼠标变成灰色。这里改用已求解的相机位姿，
把原始影像投影回网格顶点：可见性做射线遮挡测试，颜色按视角权重融合。

无影像/无相机位姿（例如 --model 注入点云）时静默跳过，保留 DC 顶点色。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from app.utils.logger import get_logger

log = get_logger("PHOTOTEX")

# 单顶点最多融合的视角数；视角越多越平滑，但遮挡测试成本线性增长
MAX_VIEWS = 4
# 法线朝向阈值：低于该余弦值的视角不参与贴色（避免背面/掠射）
MIN_FACING = 0.12


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92,
                    np.power((c + 0.055) / 1.055, 2.4))


def _bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """按像素坐标双线性采样，返回 (N,3) sRGB 0-1。"""
    h, w = img.shape[:2]
    x0 = np.clip(np.floor(u).astype(np.int64), 0, w - 1)
    y0 = np.clip(np.floor(v).astype(np.int64), 0, h - 1)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = np.clip(u - x0, 0.0, 1.0)[:, None]
    fy = np.clip(v - y0, 0.0, 1.0)[:, None]
    c00 = img[y0, x0]
    c10 = img[y0, x1]
    c01 = img[y1, x0]
    c11 = img[y1, x1]
    return ((1 - fx) * (1 - fy) * c00 + fx * (1 - fy) * c10
            + (1 - fx) * fy * c01 + fx * fy * c11)


def _load_cameras(model_dir: Path, images_dir: Path) -> List[dict]:
    path = Path(model_dir) / "cameras.json"
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("影像贴色: cameras.json 解析失败: %s", exc)
        return []
    cams = []
    for c in raw:
        try:
            name = str(c["img_name"])
            if not (images_dir / name).is_file():
                continue
            cams.append({
                "img_name": name,
                "R": np.asarray(c["rotation"], dtype=np.float64).reshape(3, 3),
                "pos": np.asarray(c["position"], dtype=np.float64).reshape(3),
                "fx": float(c["fx"]), "fy": float(c["fy"]),
                "w": int(c["width"]), "h": int(c["height"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return cams


def colorize_from_views(mesh, dataset_dir: Path, model_dir: Path,
                        max_views: int = MAX_VIEWS) -> bool:
    """把原始影像颜色投影到网格顶点；成功返回 True。

    1. 对全部相机做向量化投影，按 视角余弦² / 距离² 打分；
    2. 每顶点取分数最高的 max_views 个相机；
    3. 用射线遮挡测试剔除被前景挡住的视角；
    4. 双线性采样 + 权重融合，写入 mesh.vertex_colors（线性空间）。

    任何异常都只记录日志并返回 False，不影响网格主流程。
    """
    dataset_dir = Path(dataset_dir)
    model_dir = Path(model_dir)
    images_dir = dataset_dir / "images"
    if not images_dir.is_dir():
        log.info("影像贴色跳过: 缺少影像目录 %s", images_dir)
        return False
    cams = _load_cameras(model_dir, images_dir)
    if len(cams) < 2:
        log.info("影像贴色跳过: 可用相机不足 (%d)", len(cams))
        return False
    if not mesh.has_vertices():
        return False

    try:
        import cv2
        import open3d as o3d
    except ImportError as exc:
        log.info("影像贴色跳过: 依赖不可用 (%s)", exc)
        return False

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    nv = len(verts)
    if nv < 50:
        return False
    mesh.compute_vertex_normals()
    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)

    bbox = verts.max(axis=0) - verts.min(axis=0)
    diag = float(np.linalg.norm(bbox)) or 1.0
    eps = 0.002 * diag

    pos = np.stack([c["pos"] for c in cams])          # (C,3)
    rots = np.stack([c["R"] for c in cams])           # (C,3,3)
    fx = np.asarray([c["fx"] for c in cams])
    fy = np.asarray([c["fy"] for c in cams])
    cw = np.asarray([c["w"] for c in cams])
    ch = np.asarray([c["h"] for c in cams])

    # ---- 1) 逐相机向量化投影 -------------------------------------- #
    scores = np.full((len(cams), nv), -np.inf, dtype=np.float32)
    proj_u = np.zeros((len(cams), nv), dtype=np.float32)
    proj_v = np.zeros((len(cams), nv), dtype=np.float32)
    for ci, cam in enumerate(cams):
        delta = verts - cam["pos"]                     # 相机 → 顶点
        pc = delta @ cam["R"]                          # R^T·delta
        z = pc[:, 2]
        valid = z > 1e-4
        if not np.any(valid):
            continue
        zz = np.where(valid, z, 1.0)
        u = cam["fx"] * pc[:, 0] / zz + cam["w"] / 2.0
        v = cam["fy"] * pc[:, 1] / zz + cam["h"] / 2.0
        inside = (valid & (u >= 1) & (u < cam["w"] - 1)
                  & (v >= 1) & (v < cam["h"] - 1))
        dist = np.linalg.norm(delta, axis=1)
        dist = np.where(dist > 1e-6, dist, 1e-6)
        # 网格法向朝向可能整体内向（泊松开曲面), 用 |cos| 判朝向,
        # 最终由射线遮挡测试决定哪个面真正可见。
        facing = np.abs(np.einsum("ij,ij->i", normals,
                                  -delta / dist[:, None]))
        ok = inside & (facing > MIN_FACING)
        if not np.any(ok):
            continue
        s = (facing ** 2) / (dist ** 2)
        scores[ci, ok] = s[ok].astype(np.float32)
        proj_u[ci, ok] = u[ok].astype(np.float32)
        proj_v[ci, ok] = v[ok].astype(np.float32)

    finite = np.isfinite(scores)
    if not np.any(finite):
        log.info("影像贴色跳过: 无有效投影")
        return False

    k = min(max_views, len(cams))
    order = np.argpartition(-scores, k - 1, axis=0)[:k]       # (k,nv)
    picked = np.take_along_axis(scores, order, axis=0)
    valid_slots = np.isfinite(picked)

    # ---- 2) 遮挡测试 ----------------------------------------------- #
    try:
        scene = o3d.t.geometry.RaycastingScene()
        scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    except Exception as exc:  # noqa: BLE001 —— 无射线场景则退化为不做遮挡测试
        log.info("影像贴色: 遮挡测试不可用, 直接按视角融合 (%s)", exc)
        scene = None

    origins = []
    dirs = []
    dists = []
    pair_v = []
    for si in range(k):
        vids = np.nonzero(valid_slots[si])[0]
        cids = order[si][vids]
        if len(vids) == 0:
            continue
        delta = pos[cids] - verts[vids]
        dist = np.linalg.norm(delta, axis=1)
        safe = dist > 1e-6
        vids, cids, delta, dist = vids[safe], cids[safe], delta[safe], dist[safe]
        unit = delta / dist[:, None]
        dirs.append(unit)
        # 沿视线方向前移 epsilon, 避免从曲面内部起射被自身挡住
        origins.append(verts[vids] + unit * eps)
        dists.append(dist)
        pair_v.append(vids)

    visible = np.ones(sum(len(x) for x in pair_v), dtype=bool)
    if scene is not None and pair_v:
        origins = np.vstack(origins)
        dirs = np.vstack(dirs)
        dists = np.concatenate(dists)
        try:
            rays = np.hstack([origins, dirs]).astype(np.float32)
            ans = scene.cast_rays(o3d.core.Tensor(rays))
            t_hit = ans["t_hit"].numpy()
            visible = ~np.isfinite(t_hit) | (t_hit >= dists - 2 * eps)
        except Exception as exc:  # noqa: BLE001
            log.info("影像贴色: 射线检测失败, 跳过遮挡 (%s)", exc)
            visible = np.ones(len(origins), dtype=bool)

    # ---- 3) 影像采样与融合 ----------------------------------------- #
    img_cache: Dict[int, np.ndarray] = {}
    sum_w = np.zeros(nv, dtype=np.float64)
    sum_c = np.zeros((nv, 3), dtype=np.float64)
    samples = 0
    offset = 0
    for si in range(k):
        vids = pair_v[si]
        if len(vids) == 0:
            continue
        vis = visible[offset:offset + len(vids)]
        offset += len(vids)
        vids = vids[vis]
        if len(vids) == 0:
            continue
        cids = order[si][vids]
        for ci in np.unique(cids):
            sel = cids == ci
            vv = vids[sel]
            img = img_cache.get(int(ci))
            if img is None:
                bgr = cv2.imread(str(images_dir / cams[int(ci)]["img_name"]))
                if bgr is None:
                    continue
                img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
                img_cache[int(ci)] = img
            rgb = _bilinear(img, proj_u[ci, vv].astype(np.float64),
                            proj_v[ci, vv].astype(np.float64))
            w = picked[si, vv].astype(np.float64)
            sum_c[vv] += rgb * w[:, None]
            sum_w[vv] += w
            samples += len(vv)

    covered = sum_w > 0
    if not np.any(covered):
        log.info("影像贴色失败: 所有顶点均无可见视角")
        return False
    colors = np.zeros((nv, 3), dtype=np.float64)
    colors[covered] = _srgb_to_linear(
        np.clip(sum_c[covered] / sum_w[covered, None], 0.0, 1.0))
    # 投影未覆盖的顶点（遮挡/边缘）用最近覆盖顶点颜色补齐:
    # 直接保留 TSDF/DC 颜色会在接触面留下白色块状伪影。
    filled = 0
    if not covered.all():
        try:
            src = o3d.geometry.PointCloud()
            src.points = o3d.utility.Vector3dVector(verts[covered])
            src.colors = o3d.utility.Vector3dVector(colors[covered])
            tree = o3d.geometry.KDTreeFlann(src)
            covered_colors = colors[covered]
            for i in np.nonzero(~covered)[0]:
                _k, idx, _d = tree.search_knn_vector_3d(verts[i], 1)
                if idx:
                    colors[i] = covered_colors[idx[0]]
                    filled += 1
        except Exception as exc:  # noqa: BLE001
            log.info("影像贴色: 未覆盖顶点补色跳过 (%s)", exc)
    mesh.vertex_colors = o3d.utility.Vector3dVector(np.clip(colors, 0, 1))
    pct = 100.0 * covered.mean()
    log.info("影像贴色完成: %d/%d 顶点 (%.1f%%), 补色 %d, "
             "平均 %.2f 视角/顶点",
             int(covered.sum()), nv, pct, filled,
             samples / max(int(covered.sum()), 1))
    return True
