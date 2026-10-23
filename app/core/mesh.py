"""泼溅点云 → 网格：SH-DC 顶点色 / 不透明度过滤 / 泊松重建 / 密度裁剪 / LOD。

3DGS 输出的 point_cloud.ply 每个高斯含:
    x y z | nx ny nz | f_dc_0..2 (球谐直流项) | opacity | scale_0..2 | rot_0..3
RGB 与 f_dc 的关系（官方渲染实现）: rgb = clip(0.5 + SH_C0 * f_dc), SH_C0 = 0.28209479
"""
from __future__ import annotations

import numpy as np
from pathlib import Path
from typing import List, Optional

from app.utils.ply_io import PlyData

from app.utils.logger import get_logger

log = get_logger("MESH")

SH_C0 = 0.28209479177387814


class MeshError(RuntimeError):
    pass


class SplatMeshExtractor:
    def __init__(self, poisson_depth: int = 10,
                 opacity_threshold: float = 0.10,
                 density_quantile: float = 0.01):
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
    @staticmethod
    def _normal_radius(points: np.ndarray) -> float:
        """按最近邻间距估计法向搜索半径（对场景尺度自适应）。"""
        try:
            import open3d as o3d
        except ImportError:
            return 0.1
        n = len(points)
        if n < 100:
            return 0.1
        rng = np.random.default_rng(0)
        sample = points[rng.choice(n, min(3000, n), replace=False)]
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(sample)
        tree = o3d.geometry.KDTreeFlann(pcd)
        ds = []
        for i in range(len(sample)):
            cnt, _idx, d2 = tree.search_knn_vector_3d(sample[i], 2)
            if cnt > 1:
                ds.append(np.sqrt(d2[1]))
        if not ds:
            return 0.1
        median = float(np.median(ds))
        diag = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
        return float(np.clip(median, max(0.01, 0.002 * diag), 0.1 * diag))

    # ------------------------------------------------------------------ #
    def to_mesh_obj(self, points: np.ndarray, colors: np.ndarray,
                    obj_path: Path, base_name: str = "usf_scene",
                    lod_ratios: List[float] = (1.0, 0.5, 0.25),
                    dataset_dir: Optional[Path] = None,
                    model_dir: Optional[Path] = None) -> List[Path]:
        """泊松重建 + 顶点色回传 + 影像投影贴色 + LOD 减面，输出 OBJ 系列。"""
        try:
            import open3d as o3d
        except ImportError as exc:
            raise MeshError(
                "open3d 不可用（网格重建必需）。请在主界面「依赖体检」中一键安装, "
                f"或手动: pip install open3d；根因: {exc}") from exc

        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(points)
        pcd.colors = o3d.utility.Vector3dVector(colors)
        # 法向估计半径按点间距自适应: 固定 0.6 在稠密重建上会把鼠标曲面
        # 平滑成团, 固定过小又会在稀疏区得到噪声法向。取中位最近邻的 ~1x。
        radius = self._normal_radius(points)
        log.info("法向估计半径: %.3f (自适应)", radius)
        pcd.estimate_normals(
            search_param=o3d.geometry.KDTreeSearchParamHybrid(
                radius=radius, max_nn=30))
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
        # 原始影像投影贴色: f_dc 对黑色高光物体会偏灰, 用相机影像才是
        # 接近视频的真实外观。缺少相机位姿/影像时自动跳过。
        if dataset_dir is not None and model_dir is not None:
            try:
                from app.core.phototexture import colorize_from_views
                colorize_from_views(mesh, Path(dataset_dir), Path(model_dir))
            except Exception as exc:  # noqa: BLE001 —— 贴色失败不影响几何
                log.warning("影像投影贴色失败, 保留 DC 顶点色: %s", exc)
        mesh.compute_vertex_normals()

        obj_path.parent.mkdir(parents=True, exist_ok=True)
        o3d.io.write_triangle_mesh(
            str(obj_path), mesh,
            write_vertex_colors=True, write_ascii=False, compressed=False)
        outputs = [obj_path]

        total_tris = len(mesh.triangles)
        for i, ratio in enumerate(lod_ratios[1:], start=1):
            # 小网格也必须真实减面: 旧实现 max(1000, ...) 在 <1000 面时会
            # 把目标面数抬到超过原网格, 导致 LOD1/LOD2 与原级完全相同。
            target = max(1, min(total_tris - 1, int(total_tris * ratio)))
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
    def _prune_components(mesh, tri_clusters: np.ndarray,
                          n_clusters: int) -> None:
        """智能连通块保留策略（碎片化网格）。

        旧策略只保留最大连通块, 在“主体被切成大量小块”的扫描网格上会
        只剩几十个面；直接跳过又会让远处飞点进入交付。这里改为综合评分:

        1. 最大块无条件保留, 面数前 5 的块也保留（防止近邻判定误删部件）;
        2. 其余块需同时满足“面数 >= max(4, 0.2%)”且
           （质心在主包围盒外扩 35% 内 或 自身包围盒对角线 >= 主体 5%）;
        3. 若该策略会删除超过 70% 的面, 判定为“碎片化主体”并整体跳过,
           保证不会把主体删成残片。

        这样既能去掉远处小飞点, 又能保留主体周围的真实碎片。
        """
        counts = np.bincount(tri_clusters, minlength=n_clusters)
        total = int(counts.sum())
        if total == 0:
            return
        verts = np.asarray(mesh.vertices)
        tris = np.asarray(mesh.triangles)
        if len(tris) == 0:
            return
        tri_verts = verts[tris]                       # (T, 3, 3)
        tri_min = tri_verts.min(axis=1)               # (T, 3)
        tri_max = tri_verts.max(axis=1)
        cmin = np.full((n_clusters, 3), np.inf)
        cmax = np.full((n_clusters, 3), -np.inf)
        np.minimum.at(cmin, tri_clusters, tri_min)
        np.maximum.at(cmax, tri_clusters, tri_max)

        main = int(np.argmax(counts))
        main_diag = float(np.linalg.norm(cmax[main] - cmin[main])) or 1.0
        diag = np.linalg.norm(cmax - cmin, axis=1)
        centroid = (cmin + cmax) * 0.5
        margin = 0.35 * main_diag
        near_main = (
            np.all(centroid >= cmin[main] - margin, axis=1)
            & np.all(centroid <= cmax[main] + margin, axis=1)
        )
        min_faces = max(4, int(total * 0.002))
        keep = (counts >= min_faces) & (near_main | (diag >= 0.05 * main_diag))
        keep[main] = True
        top = np.argsort(counts)[-5:]
        keep[top] = True

        keep_faces = int(counts[keep].sum())
        removed = int(n_clusters - int(keep.sum()))
        if removed <= 0:
            log.info("细化: 无碎片可移除 (%d 个连通块)", n_clusters)
            return
        if keep_faces < total * 0.3:
            log.info(
                "细化: 智能保留会删除过多面 (%d/%d), 判定为主体碎片化, 跳过移除",
                total - keep_faces, total)
            return
        keep_ids = np.where(keep)[0]
        mesh.remove_triangles_by_mask(~np.isin(tri_clusters, keep_ids))
        mesh.remove_unreferenced_vertices()
        log.info(
            "细化: 智能保留 %d/%d 连通块, 移除 %d 个碎片, 面 %d→%d",
            len(keep_ids), n_clusters, removed, total, len(mesh.triangles))

    # ------------------------------------------------------------------ #
    @staticmethod
    def _refine(mesh):
        """网格细化: 去小碎片 / 去退化面与顶点 / Taubin 平滑。

        泊松重建常产生远离主体的低密度碎片与高频噪点 —— 逐项尝试,
        open3d 版本差异用 try/except 隔离, 任一步失败不影响其余。
        """
        import open3d as o3d
        try:   # 智能移除连通碎片（保留主体 + 近邻/足够大的碎片）
            with o3d.utility.VerbosityContextManager(o3d.utility.VerbosityLevel.Error):
                # Open3D 0.20 返回 3 元组 (每三角形簇, 每簇面数列表, 每簇面积);
                # 旧版返回 2 元组。n_clusters 是“簇的个数”, 不是面数列表。
                cluster_ret = mesh.cluster_connected_triangles()
                tri_clusters = cluster_ret[0]
                second = cluster_ret[1]
                n_clusters = len(second) if hasattr(second, "__len__") else int(second)
            tri_clusters = np.asarray(tri_clusters)
            if n_clusters > 1:
                SplatMeshExtractor._prune_components(mesh, tri_clusters, n_clusters)
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
            # 10 次会把鼠标曲面细节抹平; 2 次足以去掉泊松高频噪点
            mesh = mesh.filter_smooth_taubin(number_of_iterations=2)
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
            lod_ratios: Optional[List[float]] = None,
            dataset_dir: Optional[Path] = None,
            model_dir: Optional[Path] = None) -> List[Path]:
        points, colors = self.load_ply(ply_path)
        if len(points) < 1000:
            raise MeshError(
                f"有效高斯过少 ({len(points)}), 重建质量无法保证。"
                f"请提高抽帧数量或增加训练迭代")
        lod_ratios = lod_ratios or [1.0, 0.5, 0.25]
        return self.to_mesh_obj(points, colors, out_dir / f"{base_name}.obj",
                                base_name, lod_ratios,
                                dataset_dir=dataset_dir, model_dir=model_dir)
