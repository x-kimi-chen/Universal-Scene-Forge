"""3DGS 深度图 → TSDF 网格重建（USF 自研, MIT）。

泊松重建把高斯中心当点云，遇到半透明/高光主体（黑色鼠标）容易只重建出
背景薄片。本模块改走深度路线：用训练好的 3DGS 渲染每个相机视角的
alpha 归一化深度，再用 Open3D 的 ScalableTSDFVolume 融合出封闭表面。

依赖用户本地的 Inria 仓库与训练 venv（深度渲染子进程），不随包分发，
与 LICENSE_COMPLIANCE_CHANGES.md §3.1/§3.2 的隔离要求一致。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional

from app.utils.logger import get_logger
from app.utils.paths import script_path

log = get_logger("TSDF")

LogCb = Optional[Callable[[str], None]]


class TsdfError(RuntimeError):
    pass


def _emit(cb: LogCb, msg: str) -> None:
    log.info(msg)
    if cb:
        cb(msg)


def _iteration_from_ply(ply: Path) -> int:
    name = Path(ply).parent.name
    if name.startswith("iteration_"):
        try:
            return int(name.split("_", 1)[1])
        except ValueError:
            pass
    return -1


def _run_depth(python: str, repo: Path, script: Path, model_dir: Path,
               dataset_dir: Path, out_dir: Path, iteration: int,
               timeout_s: int, log_cb: LogCb, stop_event=None) -> None:
    cmd = [
        python, str(script),
        "-m", str(model_dir.resolve()),
        "-s", str(dataset_dir.resolve()),
        "--iteration", str(iteration),
        "--out_dir", str(out_dir.resolve()),
        "--scale", "1.0",
        "--depth_trunc", "30",
        "--quiet",
    ]
    from app.utils.security import scrubbed_env
    env = scrubbed_env()
    env["PYTHONUNBUFFERED"] = "1"
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    _emit(log_cb, "3DGS 深度渲染: " + " ".join(cmd[:3]) + " ...")
    proc = subprocess.Popen(
        cmd, cwd=str(repo), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1,
        creationflags=creation)
    started = time.monotonic()
    tail: List[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                if len(tail) > 40:
                    tail.pop(0)
            if stop_event is not None and stop_event.is_set():
                proc.kill()
                raise TsdfError("用户取消")
            if time.monotonic() - started > timeout_s:
                proc.kill()
                raise TsdfError(f"深度渲染超时 ({timeout_s}s)")
        code = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if code != 0:
        raise TsdfError(
            f"深度渲染退出码 {code}\n—— 输出尾部 ——\n" + "\n".join(tail))


def _transfer_colors(src, dst) -> None:
    import numpy as np
    import open3d as o3d
    if not src.has_vertex_colors() or len(dst.vertices) == 0:
        return
    tree = o3d.geometry.KDTreeFlann(src)
    colors = np.zeros((len(dst.vertices), 3))
    src_colors = np.asarray(src.vertex_colors)
    for i, v in enumerate(dst.vertices):
        _k, idx, _d = tree.search_knn_vector_3d(v, 1)
        colors[i] = src_colors[idx[0]] if idx else 0.5
    dst.vertex_colors = o3d.utility.Vector3dVector(colors)


def _ply_bbox(ply_path: Path):
    """读取高斯点云包围盒（0.5%-99.5% 分位，忽略飞点）。"""
    import numpy as np
    from app.utils.ply_io import PlyData
    props = PlyData.read(str(ply_path))["vertex"].data
    if not all(k in props.dtype.names for k in ("x", "y", "z")):
        return None
    pts = np.vstack([props["x"], props["y"], props["z"]]).T.astype(np.float64)
    lo = np.percentile(pts, 0.5, axis=0)
    hi = np.percentile(pts, 99.5, axis=0)
    return lo, hi


def _uniform_tsdf(views, tmp_dir: Path, depth_unit: float, max_depth: float,
                  bbox, trunc_ratio: float = 3.5):
    """Open3D 0.20 的 ScalableTSDFVolume 会静默产出空网格（实测），
    回退到 UniformTSDFVolume：体积覆盖 [0, L]，把场景平移到体积内。

    bbox = (lo, hi) 来自高斯点云；返回已平移回原世界坐标的 legacy mesh。
    """
    import numpy as np
    import open3d as o3d

    lo, hi = bbox
    span = hi - lo
    length = float(np.max(span)) * 1.25
    resolution = int(np.clip(length / 0.0375, 320, 650))
    margin = 0.05 * length
    length = max(length, float(np.max(span)) + 2 * margin)
    voxel = length / resolution
    trunc = trunc_ratio * voxel
    offset = lo - margin
    _emit(None, f"UniformTSDF: L={length:.2f} res={resolution} "
                f"voxel={voxel:.4f} offset={offset.round(2).tolist()}")
    volume = o3d.pipelines.integration.UniformTSDFVolume(
        length=float(length), resolution=int(resolution),
        sdf_trunc=float(trunc),
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8)
    T = np.eye(4)
    T[:3, 3] = offset
    for v in views:
        depth = np.load(tmp_dir / "depth" / f"{v['index']:05d}.npy")
        depth = np.nan_to_num(depth.astype(np.float32), nan=0.0,
                              posinf=0.0, neginf=0.0)
        depth[depth > max_depth * 1.5] = 0.0
        color = o3d.io.read_image(
            str(tmp_dir / "color" / f"{v['index']:05d}.png"))
        depth_u16 = np.clip(depth * depth_unit, 0, 65535).astype(np.uint16)
        depth_img = o3d.geometry.Image(depth_u16)
        intr = o3d.camera.PinholeCameraIntrinsic(
            v["width"], v["height"], v["fx"], v["fy"], v["cx"], v["cy"])
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            color, depth_img, depth_scale=float(depth_unit),
            depth_trunc=65535.0, convert_rgb_to_intensity=False)
        volume.integrate(rgbd, intr, np.asarray(v["w2c"], np.float64) @ T)
    mesh = volume.extract_triangle_mesh()
    mesh.translate(offset)
    mesh.compute_vertex_normals()
    return mesh


def build_mesh_from_depth(ply_path: Path, model_dir: Path, dataset_dir: Path,
                          out_dir: Path, gs_repo: Path, gs_python: str,
                          lod_ratios: Optional[List[float]] = None,
                          max_faces: int = 150_000,
                          timeout_s: int = 1800,
                          log_cb: LogCb = None, stop_event=None) -> List[Path]:
    """渲染深度 → TSDF 融合 → 平滑/减面 → 影像贴色 → OBJ 系列。"""
    import numpy as np

    ply_path = Path(ply_path)
    model_dir = Path(model_dir)
    dataset_dir = Path(dataset_dir)
    out_dir = Path(out_dir)
    repo = Path(gs_repo)
    python = str(gs_python or "")
    if not python or not Path(python).is_file():
        raise TsdfError("训练解释器不可用")
    if not (repo / "train.py").is_file():
        raise TsdfError(f"缺少 3DGS 仓库: {repo}")
    script = script_path("gaussian", "render_depth.py")
    if not script.is_file():
        raise TsdfError(f"缺少深度渲染脚本: {script}")
    if not (model_dir / "cameras.json").is_file():
        raise TsdfError("缺少 cameras.json（外部模型无相机位姿）")

    try:
        import open3d as o3d
    except ImportError as exc:
        raise TsdfError(f"open3d 不可用: {exc}") from exc

    from app.utils import licensing
    licensing.require_3dgs_license(context="TSDF 深度渲染")

    iteration = _iteration_from_ply(ply_path)
    lod_ratios = lod_ratios or [1.0, 0.5, 0.25]
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="usf_tsdf_") as tmp:
        tmp_dir = Path(tmp)
        _run_depth(python, repo, script, model_dir, dataset_dir, tmp_dir,
                   iteration, timeout_s, log_cb, stop_event)
        views = json.loads((tmp_dir / "views.json").read_text(
            encoding="utf-8"))
        if not views:
            raise TsdfError("深度渲染未产出视角")

        # 场景尺度自适应体素: 取若干帧有效深度中位数 / 240
        # （实测 0.025-0.03 能在鼠标曲面细节与噪声之间取得平衡）
        meds = []
        maxes = []
        for v in views[::max(1, len(views) // 8)]:
            d = np.load(tmp_dir / "depth" / f"{v['index']:05d}.npy")
            vals = d[d > 0]
            if vals.size:
                meds.append(float(np.median(vals)))
                maxes.append(float(np.quantile(vals, 0.999)))
        med_depth = float(np.median(meds)) if meds else 5.0
        max_depth = float(np.max(maxes)) if maxes else med_depth * 2
        # Open3D 各版本对 float 深度图支持不一致（0.20 会静默融合为空）；
        # 统一转 uint16 毫米图，depth_scale 按场景尺度自适应。
        depth_unit = 60000.0 / max(max_depth, 1e-6)
        voxel = float(np.clip(med_depth / 240.0, 0.008, 0.2))
        # 3.5x 截断可闭合鼠标与垫子接触处的薄缝（白色孔洞），
        # 2 次 Taubin 平滑去掉融合噪声，再减面到目标面数。
        trunc = 3.5 * voxel
        _emit(log_cb, f"TSDF 体素 {voxel:.3f} (中位深度 {med_depth:.2f}), "
                      f"截断 {trunc:.3f}")

        volume = o3d.pipelines.integration.ScalableTSDFVolume(
            voxel_length=voxel, sdf_trunc=trunc,
            color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8)
        integrated = 0
        for v in views:
            if stop_event is not None and stop_event.is_set():
                raise TsdfError("用户取消")
            depth = np.load(tmp_dir / "depth" / f"{v['index']:05d}.npy")
            depth = np.nan_to_num(depth.astype(np.float32), nan=0.0,
                                  posinf=0.0, neginf=0.0)
            depth[depth > max_depth * 1.5] = 0.0
            color = o3d.io.read_image(
                str(tmp_dir / "color" / f"{v['index']:05d}.png"))
            depth_u16 = np.clip(depth * depth_unit, 0, 65535).astype(np.uint16)
            depth_img = o3d.geometry.Image(depth_u16)
            intr = o3d.camera.PinholeCameraIntrinsic(
                v["width"], v["height"], v["fx"], v["fy"],
                v["cx"], v["cy"])
            rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
                color, depth_img, depth_scale=float(depth_unit),
                depth_trunc=65535.0,
                convert_rgb_to_intensity=False)
            volume.integrate(rgbd, intr, np.asarray(v["w2c"], np.float64))
            integrated += 1
        _emit(log_cb, f"TSDF 融合 {integrated} 帧完成")

        mesh = volume.extract_triangle_mesh()
        mesh.compute_vertex_normals()
        if len(mesh.triangles) == 0:
            # Open3D 0.20 的 ScalableTSDFVolume 实测会静默返回空网格
            _emit(log_cb, "ScalableTSDF 空结果, 回退 UniformTSDFVolume")
            bbox = _ply_bbox(ply_path)
            if bbox is not None:
                mesh = _uniform_tsdf(views, tmp_dir, depth_unit,
                                     max_depth, bbox)
                _emit(log_cb, f"UniformTSDF 网格: {len(mesh.vertices)} 顶点 / "
                              f"{len(mesh.triangles)} 面")
        if len(mesh.triangles) == 0:
            raise TsdfError("TSDF 未提取到网格")
        _emit(log_cb, f"TSDF 网格: {len(mesh.vertices)} 顶点 / "
                      f"{len(mesh.triangles)} 面")

        try:
            ret = mesh.cluster_connected_triangles()
            clusters = np.asarray(ret[0])
            counts = np.bincount(clusters)
            keep = np.where(counts >= max(200, counts.max() * 0.01))[0]
            mesh.remove_triangles_by_mask(~np.isin(clusters, keep))
            mesh.remove_unreferenced_vertices()
            mesh.compute_vertex_normals()
            _emit(log_cb, f"TSDF 碎片清理: 保留 {len(keep)} 个连通块, "
                          f"{len(mesh.triangles)} 面")
        except Exception as exc:  # noqa: BLE001
            _emit(log_cb, f"TSDF 碎片清理跳过: {exc}")

        try:
            mesh = mesh.filter_smooth_taubin(number_of_iterations=2)
            mesh.compute_vertex_normals()
        except Exception as exc:  # noqa: BLE001
            _emit(log_cb, f"TSDF 平滑跳过: {exc}")

        if len(mesh.triangles) > max_faces:
            src = mesh
            mesh = mesh.simplify_quadric_decimation(max_faces)
            if not mesh.has_vertex_colors() and src.has_vertex_colors():
                _transfer_colors(src, mesh)
            _emit(log_cb, f"TSDF 减面: {len(mesh.triangles)} 面")

        try:
            from app.core.phototexture import colorize_from_views
            if colorize_from_views(mesh, dataset_dir, model_dir):
                _emit(log_cb, "影像投影贴色完成")
        except Exception as exc:  # noqa: BLE001
            _emit(log_cb, f"影像贴色跳过: {exc}")

        mesh.compute_vertex_normals()
        base = out_dir / "usf_scene.obj"
        o3d.io.write_triangle_mesh(str(base), mesh, write_vertex_colors=True,
                                   write_ascii=False, compressed=False)
        outputs = [base]
        total = len(mesh.triangles)
        for i, ratio in enumerate(lod_ratios[1:], start=1):
            target = max(1, min(total - 1, int(total * ratio)))
            lod = mesh.simplify_quadric_decimation(target)
            if not lod.has_vertex_colors() and mesh.has_vertex_colors():
                _transfer_colors(mesh, lod)
            lod.compute_vertex_normals()
            lod_path = out_dir / f"usf_scene_lod{i}.obj"
            o3d.io.write_triangle_mesh(
                str(lod_path), lod, write_vertex_colors=True,
                write_ascii=False, compressed=False)
            outputs.append(lod_path)
        _emit(log_cb, f"TSDF 网格产物: {len(outputs)} 个")
        return outputs
