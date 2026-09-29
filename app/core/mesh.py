"""泼溅点云 → 网格：SH-DC 顶点色 / 不透明度过滤 / 泊松重建 / 密度裁剪 / LOD。

3DGS 输出的 point_cloud.ply 每个高斯含:
    x y z | nx ny nz | f_dc_0..2 (球谐直流项) | opacity | scale_0..2 | rot_0..3
RGB 与 f_dc 的关系（官方渲染实现）: rgb = clip(0.5 + SH_C0 * f_dc), SH_C0 = 0.28209479
"""
from __future__ import annotations

import numpy as np
from pathlib import Path
from typing import List, Optional

from plyfile import PlyData

from app.utils.logger import get_logger

log = get_logger("MESH")

SH_C0 = 0.28209479177387814


class MeshError(RuntimeError):
    pass


class SplatMeshExtractor:
    def __init__(self, poisson_depth: int = 9,
                 opacity_threshold: float = 0.35,
                 density_quantile: float = 0.02):
        self.depth = poisson_depth
        self.opacity_threshold = opacity_threshold
        self.density_quantile = density_quantile

    # ------------------------------------------------------------------ #
    def load_ply(self, ply_path: Path):
        ply = PlyData.read(str(ply_path))
        vertex = ply["vertex"]
        props = vertex.data
        if "x" not in props.dtype.names:
            raise MeshError(f"非法高斯 PLY: {ply_path}")

        points = np.vstack([props["x"], props["y"], props["z"]]).T.astype(np.float64)
        n = len(points)

        # 顶点色（球谐直流项 → RGB）
        if all(k in props.dtype.names for k in ("f_dc_0", "f_dc_1", "f_dc_2")):
            rgb = 0.5 + SH_C0 * np.vstack(
                [props["f_dc_0"], props["f_dc_1"], props["f_dc_2"]]).T
            colors = np.clip(rgb, 0.0, 1.0)
        else:
            colors = np.full((n, 3), 0.6, dtype=np.float64)
            log.warning("PLY 无 SH 直流项, 使用灰色顶点")

        # 不透明度过滤（sigmoid 后阈值裁剪低置信高斯）
        mask = np.ones(n, dtype=bool)
        if "opacity" in props.dtype.names:
            opacity = 1.0 / (1.0 + np.exp(-np.asarray(props["opacity"], dtype=np.float64)))
            mask = opacity > self.opacity_threshold
            log.info("不透明度过滤: %d/%d 高斯保留 (%.0f%%)",
                     int(mask.sum()), n, 100.0 * mask.sum() / max(n, 1))

        return points[mask], colors[mask]

    # ------------------------------------------------------------------ #
    def to_mesh_obj(self, points: np.ndarray, colors: np.ndarray,
                    obj_path: Path, base_name: str = "usf_scene",
                    lod_ratios: List[float] = (1.0, 0.5, 0.25)) -> List[Path]:
        """泊松重建 + 顶点色回传 + LOD 减面，输出 OBJ 系列。"""
        try:
            import open3d as o3d
        except ImportError as exc:
            raise MeshError(
                "open3d 未安装（网格重建必需）。请在主界面「依赖体检」中一键安装, "
                "或手动: pip install open3d") from exc

        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(points)
        pcd.colors = o3d.utility.Vector3dVector(colors)
        pcd.estimate_normals(
            search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=0.6, max_nn=30))
        pcd.orient_normals_consistent_tangent_plane(k=30)

        log.info("泊松重建 (depth=%d) ...", self.depth)
        mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
            pcd, depth=self.depth, linear_fit=True)
        densities = np.asarray(densities)

        # 密度低分位裁剪（去除泊松外延假面）
        if densities.size == len(mesh.vertices):
            keep = densities > np.quantile(densities, self.density_quantile)
            mesh.remove_vertices_by_mask(keep)
        log.info("网格: %d 顶点 / %d 面", len(mesh.vertices), len(mesh.triangles))

        # Q-02 网格细化: 小碎片移除 → 退化几何清理 → Taubin 平滑
        mesh = self._refine(mesh)

        mesh = self._transfer_colors(mesh, pcd)
        mesh.compute_vertex_normals()

        obj_path.parent.mkdir(parents=True, exist_ok=True)
        o3d.io.write_triangle_mesh(
            str(obj_path), mesh,
            write_vertex_colors=True, write_ascii=False, compressed=False)
        outputs = [obj_path]

        total_tris = len(mesh.triangles)
        for i, ratio in enumerate(lod_ratios[1:], start=1):
            target = max(1_000, int(total_tris * ratio))
            lod = mesh.simplify_quadric_decimation(target)
            lod.compute_vertex_normals()
            lod_path = obj_path.with_name(f"{base_name}_lod{i}.obj")
            o3d.io.write_triangle_mesh(
                str(lod_path), lod, write_vertex_colors=True, compressed=False)
            outputs.append(lod_path)
            log.info("LOD%d: %d 面 (目标比例 %.0f%%)", i, len(lod.triangles), ratio * 100)
        return outputs

    # ------------------------------------------------------------------ #
    @staticmethod
    def _refine(mesh):
        """网格细化: 去小碎片 / 去退化面与顶点 / Taubin 平滑。

        泊松重建常产生远离主体的低密度碎片与高频噪点 —— 逐项尝试,
        open3d 版本差异用 try/except 隔离, 任一步失败不影响其余。
        """
        import open3d as o3d
        try:   # 移除小连通碎片 (保留与主体相连的最大部分)
            with o3d.utility.VerbosityContextManager(o3d.utility.VerbosityLevel.Error):
                tri_clusters, n_clusters = mesh.cluster_connected_triangles()
            tri_clusters = np.asarray(tri_clusters)
            if n_clusters > 1:
                counts = np.bincount(tri_clusters)
                keep_id = int(np.argmax(counts))
                mesh.remove_triangles_by_mask(tri_clusters != keep_id)
                mesh.remove_unreferenced_vertices()
                log.info("细化: 移除 %d 个小碎片, 保留 %d 面",
                         n_clusters - 1, len(mesh.triangles))
        except Exception as exc:  # noqa: BLE001
            log.info("细化-碎片移除跳过: %s", exc)
        try:   # 去退化三角形与未引用顶点
            mesh.remove_degenerate_triangles()
            mesh.remove_duplicated_triangles()
            mesh.remove_duplicated_vertices()
            mesh.remove_non_manifold_edges()
        except Exception as exc:  # noqa: BLE001
            log.info("细化-退化清理跳过: %s", exc)
        try:   # Taubin 平滑: 去高频噪点且不收缩形体 (比 Laplacian 更保形)
            before = len(mesh.vertices)
            mesh = mesh.filter_smooth_taubin(number_of_iterations=10)
            log.info("细化: Taubin 平滑 %d → %d 顶点", before, len(mesh.vertices))
        except Exception as exc:  # noqa: BLE001
            log.info("细化-平滑跳过: %s", exc)
        return mesh

    # ------------------------------------------------------------------ #
    @staticmethod
    def _transfer_colors(mesh, pcd):
        """泊松网格不继承点色 → 用 KDTree 最近点把高斯颜色回传到网格顶点。"""
        import open3d as o3d
        if not pcd.has_colors() or len(mesh.vertices) == 0:
            return mesh
        tree = o3d.geometry.KDTreeFlann(pcd)
        colors = np.zeros((len(mesh.vertices), 3))
        pcd_colors = np.asarray(pcd.colors)
        for i, v in enumerate(mesh.vertices):
            _, idx, _ = tree.search_knn_vector_3d(v, 1)
            colors[i] = pcd_colors[idx[0]] if idx else 0.5
        mesh.vertex_colors = o3d.utility.Vector3dVector(np.clip(colors, 0, 1))
        return mesh

    # ------------------------------------------------------------------ #
    def run(self, ply_path: Path, out_dir: Path,
            base_name: str = "usf_scene",
            lod_ratios: Optional[List[float]] = None) -> List[Path]:
        points, colors = self.load_ply(ply_path)
        if len(points) < 1000:
            raise MeshError(
                f"有效高斯过少 ({len(points)}), 重建质量无法保证。"
                f"请提高抽帧数量或增加训练迭代")
        lod_ratios = lod_ratios or [1.0, 0.5, 0.25]
        return self.to_mesh_obj(points, colors, out_dir / f"{base_name}.obj",
                                base_name, lod_ratios)
