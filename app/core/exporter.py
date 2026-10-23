"""导出分发器：按格式路由到 Blender / Open3D / UE5 桥接。"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from app.utils.logger import get_logger

log = get_logger("EXPORT")


class ExportDispatcher:
    """格式 → 执行器 分发。

    - fbx                        : Blender（权威, 嵌纹理）→ 3ds Max → Maya
                                   → Houdini → Cinema 4D 降级链
    - glb / gltf / blend         : Blender 桥接
    - obj                        : Open3D 直写（Blender 缺席时的保底路径）
    - uasset                    : UE5 桥接 (pythonscript 导入 + 自动保存)
    每种格式独立容错: 失败只记日志, 不影响其余格式。
    """

    def __init__(self, blender_bridge=None, ue5_bridge=None,
                 max_bridge=None, maya_bridge=None,
                 houdini_bridge=None, c4d_bridge=None,
                 modo_bridge=None):
        self.blender = blender_bridge
        self.ue5 = ue5_bridge
        self.max = max_bridge
        self.maya = maya_bridge
        self.houdini = houdini_bridge
        self.c4d = c4d_bridge
        self.modo = modo_bridge

    def export_all(self, mesh_obj: Path, out_dir: Path,
                   formats: List[str], base_name: str = "usf_scene",
                   lod_ratios: Optional[List[float]] = None,
                   bake_ao: bool = False,
                   texture_size: int = 4096) -> Dict[str, List[Path]]:
        # M-02: 输出目录边界校验（拒绝盘符根/系统目录, 探测可写性）
        from app.utils.security import ensure_output_dir
        out_dir = ensure_output_dir(out_dir)
        lod_ratios = lod_ratios or [1.0, 0.5, 0.25]
        results: Dict[str, List[Path]] = {}

        want = {f.lower() for f in formats}
        # OBJ 也交给 Blender（4.0+ bpy.ops.wm.obj_export）, 仅在 Blender 缺席/
        # 失败时降级 Open3D 直写。
        blender_formats = want & {"fbx", "glb", "gltf", "blend", "usd", "obj"}
        direct_formats = want - blender_formats - {"uasset"}

        if blender_formats and self.blender:
            try:
                files = self.blender.postprocess_mesh(
                    mesh_in=mesh_obj, out_dir=out_dir, base_name=base_name,
                    formats=sorted(blender_formats),
                    lod_ratios=lod_ratios, bake_ao=bake_ao,
                    texture_size=texture_size)
                for fmt in blender_formats:
                    # USD 导出器按场景内容可能产出 .usda/.usdc/.usd,
                    # 三者同属 usd 格式; 只按 fmt 字面匹配会误报"未产出"。
                    exts = {"usd": {"usd", "usda", "usdc"}}.get(
                        fmt, {fmt})
                    results[fmt] = [
                        f for f in files
                        if f.suffix.lower().lstrip(".") in exts]
            except Exception as exc:  # noqa: BLE001 容错: Blender 缺席/失败降级
                log.error("Blender 导出失败, 降级: %s", exc)

        # OBJ 降级: Blender 未产出时用 Open3D 直写（可用性优先, 风险最低）
        if "obj" in want and not results.get("obj"):
            try:
                results["obj"] = self._export_obj_lods(
                    mesh_obj, out_dir, base_name, lod_ratios)
                log.warning("OBJ 由 Open3D 直写降级导出")
            except Exception as exc:  # noqa: BLE001
                log.error("导出 obj 失败: %s", exc)

        for fmt in direct_formats:
            log.warning("暂不支持直写格式: %s", fmt)

        # FBX 降级链: Blender 缺席/失败 → 3ds Max → Maya → Houdini → C4D
        if "fbx" in want and not results.get("fbx"):
            fbx_out = out_dir / f"{base_name}.fbx"
            chain = [
                ("3ds Max", self.max,
                 lambda: [self.max.export_fbx(mesh_obj, fbx_out)]),
                ("Maya", self.maya,
                 lambda: self._maya_fbx(mesh_obj, fbx_out)),
                ("Houdini", self.houdini,
                 lambda: [self.houdini.export_fbx(mesh_obj, fbx_out)]),
                ("Cinema 4D", self.c4d,
                 lambda: [self.c4d.export_fbx(mesh_obj, fbx_out)]),
                ("Modo", self.modo,
                 lambda: [self.modo.export_fbx(mesh_obj, fbx_out)]),
            ]
            for name, bridge, fn in chain:
                if bridge is None or results.get("fbx"):
                    continue
                try:
                    results["fbx"] = fn()
                    log.info("FBX 由 %s 降级导出", name)
                except Exception as exc:  # noqa: BLE001 容错降级
                    log.error("%s FBX 导出失败: %s", name, exc)

        if "uasset" in want and self.ue5:
            try:
                files = self.ue5.import_files([mesh_obj], destination="/Game/USF")
                results["uasset"] = [Path(f) for f in files] if files else []
                if not results["uasset"]:
                    log.warning("UE5 导入未产出 uasset (检查 .uproject 与引擎日志)")
            except Exception as exc:  # noqa: BLE001
                log.error("UE5 导入失败: %s", exc)
        elif "uasset" in want:
            log.warning("uasset 需要 UE5: 在「调用路径设置」中配置引擎与 .uproject "
                        "工程后重试, 其余格式不受影响")

        # 产物校验（L-02）: 过滤不存在/零字节文件, 避免"日志成功但产物为空"
        for fmt, files in list(results.items()):
            good: List[Path] = []
            for f in files:
                try:
                    fp = Path(f)
                    if fp.is_file() and fp.stat().st_size > 0:
                        good.append(fp)
                    else:
                        log.error("产物缺失或为 0 字节: %s", fp)
                except OSError as exc:
                    log.error("产物不可读: %s (%s)", f, exc)
            results[fmt] = good
            if want and fmt in want and not good:
                log.warning("格式 %s 未产出有效文件", fmt)
        return results

    # ------------------------------------------------------------------ #
    def _maya_fbx(self, mesh_obj: Path, fbx_out: Path) -> List[Path]:
        """Maya MEL 批处理: OBJ 导入 → FBX 导出。"""
        fbx_out.parent.mkdir(parents=True, exist_ok=True)
        mel = self.maya.build_fbx_export_mel(mesh_obj, fbx_out)
        self.maya.run_mel(mel)
        if not fbx_out.exists():
            raise RuntimeError(f"Maya 未产出 {fbx_out.name}")
        return [fbx_out]

    # ------------------------------------------------------------------ #
    @staticmethod
    def _export_obj_lods(mesh_obj: Path, out_dir: Path, base_name: str,
                         lod_ratios: List[float]) -> List[Path]:
        """无 Blender 保底: 复制主 OBJ(+材质/纹理随行文件), 并用 Open3D 生成 LOD OBJ。"""
        import re as _re
        import shutil as _sh
        outputs: List[Path] = [out_dir / f"{base_name}.obj"]
        _sh.copy2(mesh_obj, outputs[0])
        # 材质随行(完整性): 只拷 .obj 会丢 mtl/纹理 → 模型"能开但没材质"。
        mtl = mesh_obj.with_suffix(".mtl")
        if mtl.exists():
            _sh.copy2(mtl, out_dir / f"{base_name}.mtl")
            try:
                text = mtl.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for ref in _re.findall(r"^\s*map_\S+\s+(.+?)\s*$", text, flags=_re.M):
                tex = mesh_obj.parent / Path(ref.strip()).name
                if tex.exists():
                    _sh.copy2(tex, out_dir / tex.name)
        if len(lod_ratios) > 1:
            try:
                import open3d as o3d
                mesh = o3d.io.read_triangle_mesh(str(mesh_obj))
                total = len(mesh.triangles)
                for i, ratio in enumerate(lod_ratios[1:], start=1):
                    # 小网格不能用 1000 面下限, 否则 LOD 不会真正减面。
                    target = max(1, min(total - 1, int(total * ratio)))
                    lod = mesh.simplify_quadric_decimation(target)
                    lod.compute_vertex_normals()
                    p = out_dir / f"{base_name}_lod{i}.obj"
                    o3d.io.write_triangle_mesh(str(p), lod, write_vertex_colors=True)
                    outputs.append(p)
            except Exception:  # noqa: BLE001 导入/生成失败(含线程内 DLL 初始化)不阻断
                log.warning("open3d 缺失或不可用, 仅导出主 OBJ")
        return outputs
