"""Universal Scene Forge — Blender 内部网格后处理脚本（核心桥接段）。

由 app/dcc/blender_bridge.py 以如下方式无头调用:
    blender --background --factory-startup --python mesh_to_fbx.py -- params.json

params.json 契约:
{
  "input":      "D:/work/usf_scene.obj",   # 输入网格 (obj/ply/fbx/glb/stl)
  "out_dir":    "D:/output",
  "base_name":  "usf_scene",
  "formats":    ["fbx", "glb", "blend"],
  "lod_ratios": [1.0, 0.5, 0.25],
  "bake_ao":    false
}

输出协议（供宿主流式解析）:
    [USF] PROGRESS <0-100> <msg>   进度
    [USF] FILE <abs path>          产物
    [USF] DONE                      正常结束
兼容 Blender 3.x / 4.x（导入操作符命名差异已做 shim）。
"""
import json
import math
import sys
from pathlib import Path

import bpy


# --------------------------------------------------------------------- #
#  输出协议
# --------------------------------------------------------------------- #
def usf_log(msg):
    print(f"[USF] {msg}", flush=True)


def usf_progress(percent, msg=""):
    print(f"[USF] PROGRESS {int(percent)} {msg}", flush=True)


def usf_file(path):
    print(f"[USF] FILE {Path(path).as_posix()}", flush=True)


def load_params():
    argv = sys.argv[sys.argv.index("--") + 1:]
    with open(argv[0], "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------- #
#  上下文兼容工具（--background 下无活动对象, 必须显式 override）
# --------------------------------------------------------------------- #
def _activate(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)


def _with_override(obj, fn):
    """3.2+ 用 temp_override, 旧版退化为直接调用。"""
    _activate(obj)
    try:
        with bpy.context.temp_override(
                object=obj,
                selected_objects=[obj],
                active_objects=[obj]):
            return fn()
    except (AttributeError, TypeError):
        return fn()


# --------------------------------------------------------------------- #
#  场景清空（--factory-startup 仍含默认 Cube/Camera/Light, 必须清除防混入导出）
# --------------------------------------------------------------------- #
def purge_default_scene():
    removed = 0
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
    if removed:
        usf_log(f"默认场景对象已清除: {removed} 个")


# --------------------------------------------------------------------- #
#  导入（4.x: wm.obj_import / 3.x: import_mesh.obj）
# --------------------------------------------------------------------- #
def import_mesh(path):
    ext = Path(path).suffix.lower()
    attempts = {
        ".obj": [("wm", "obj_import"), ("import_mesh", "obj")],
        ".ply": [("wm", "ply_import"), ("import_mesh", "ply")],
        ".stl": [("wm", "stl_import"), ("import_mesh", "stl")],
        ".fbx": [("import_scene", "fbx")],
        ".glb": [("import_scene", "gltf")],
        ".gltf": [("import_scene", "gltf")],
    }
    for namespace, opname in attempts[ext]:
        op = getattr(getattr(bpy.ops, namespace), opname, None)
        if op is None:
            continue
        try:
            op(filepath=str(path))
            usf_log(f"已导入 {Path(path).name} via bpy.ops.{namespace}.{opname}")
            return True
        except RuntimeError as exc:
            usf_log(f"导入失败 ({namespace}.{opname}): {exc}")
            if namespace == attempts[ext][-1][0]:
                raise
    return False


def mesh_objects():
    return [o for o in bpy.data.objects if o.type == "MESH"]


# --------------------------------------------------------------------- #
#  拓扑清理: 删除松散几何 / 合并重复顶点 / 统一法线
# --------------------------------------------------------------------- #
def cleanup_mesh(obj):
    def _edit_ops():
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.delete_loose(use_verts=True, use_edges=True, use_faces=True)
        bpy.ops.mesh.dissolve_degenerate(threshold=1e-5)
        bpy.ops.mesh.remove_doubles(threshold=1e-4)
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.object.mode_set(mode="OBJECT")
    _with_override(obj, _edit_ops)
    usf_log(f"拓扑清理完成: {obj.name} "
            f"({len(obj.data.vertices)} v / {len(obj.data.polygons)} f)")


# --------------------------------------------------------------------- #
#  智能UV + 顶点色材质（顶点色来自 3DGS SH-DC, 见 app/core/mesh.py）
# --------------------------------------------------------------------- #
def smart_uv(obj):
    if obj.data.uv_layers:
        return
    def _uv_ops():
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66.0),
                                  island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")
    _with_override(obj, _uv_ops)
    usf_log(f"智能UV完成: {obj.name}")


def ensure_material(obj):
    mat = bpy.data.materials.get("USF_Material")
    if mat is None:
        mat = bpy.data.materials.new("USF_Material")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return
    # 若网格带颜色属性(顶点色) → 直连 Base Color, 保留重建外观
    color_attr = obj.data.color_attributes[0] if obj.data.color_attributes else None
    if color_attr is not None:
        attr = next((n for n in nodes if n.type == "ATTRIBUTE" and
                     n.attribute_name == color_attr.name), None)
        if attr is None:
            attr = nodes.new("ShaderNodeAttribute")
            attr.attribute_name = color_attr.name
            attr.location = (bsdf.location.x - 240, bsdf.location.y + 80)
        links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        usf_log(f"顶点色材质绑定: {obj.name}.{color_attr.name}")
    else:
        bsdf.inputs["Base Color"].default_value = (0.7, 0.7, 0.7, 1.0)
    for slot in obj.material_slots:
        if slot.material == mat:
            break
    else:
        obj.data.materials.clear()
        obj.data.materials.append(mat)


# --------------------------------------------------------------------- #
#  纹理烘焙: 顶点色/材质 → UV 贴图 (真实感交付, 0.9.7)
# --------------------------------------------------------------------- #
def set_engine(name):
    bpy.context.scene.render.engine = name


def scene_render_engine():
    return bpy.context.scene.render.engine


def bake_texture(obj, mat, tex_path, size=2048):
    scene = bpy.context.scene
    """把当前材质外观烘焙到 UV 贴图, 并让 BaseColor 改用该贴图。

    前置: obj 已有 UV (smart_uv)。烘焙后 FBX/GLB 交付带真实贴图,
    观感显著优于纯顶点色。open3d 顶点色链路下等价于"外观烘焙"。
    """
    scene = bpy.context.scene
    prev_engine = scene_render_engine()
    try:
        img = bpy.data.images.new("USF_BakedTex", size, size, alpha=False)
        img.filepath_raw = str(tex_path)
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        mat.node_tree.nodes.active = tex
        tex.select = True

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        # 烘焙仅 CYCLES 支持 (EEVEE/Workbench 无头烘焙不可用)
        bpy.ops.object.mode_set(mode="OBJECT")
        set_engine("CYCLES")
        scene.cycles.samples = 1
        bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"},
                            use_clear=True, use_selected_to_active=False)
        set_engine(prev_engine)
        img.save()

        # BaseColor 改接贴图 (保留顶点色属性节点便于回退)
        bsdf = next((n for n in mat.node_tree.nodes
                     if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf:
            for link in list(mat.node_tree.links):
                if link.to_node == bsdf and link.to_socket.name == "Base Color":
                    mat.node_tree.links.remove(link)
            mat.node_tree.links.new(tex.outputs["Color"],
                                    bsdf.inputs["Base Color"])
        usf_log(f"纹理烘焙完成: {size}x{size} → {tex_path.name}")
        set_engine(prev_engine)
        return True
    except Exception as exc:  # noqa: BLE001 烘焙失败回退顶点色, 不阻断
        set_engine(prev_engine)
        usf_log(f"纹理烘焙跳过: {exc}")
        return False


# --------------------------------------------------------------------- #
#  LOD: 非破坏减面复制
# --------------------------------------------------------------------- #
def build_lod(obj, ratio, name):
    dup = obj.copy()
    dup.data = obj.data.copy()
    dup.name = name
    bpy.context.collection.objects.link(dup)
    if ratio < 1.0:
        mod = dup.modifiers.new("USF_LOD", "DECIMATE")
        mod.ratio = ratio
        def _apply():
            bpy.ops.object.modifier_apply(modifier="USF_LOD")
        _with_override(dup, _apply)
    return dup


# --------------------------------------------------------------------- #
#  可选 AO 烘焙（Cycles, 无头失败则自动跳过——不阻断导出）
# --------------------------------------------------------------------- #
def bake_ao_passthrough():
    try:
        scene = bpy.context.scene
        prev_engine = scene.render.engine   # 还原原引擎, 兼容 2.8x~5.x 名称差异
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 64
        usf_log("AO 烘焙跳过: 无头模式下需显式提供烘焙目标图像, 已按顶点色输出")
        scene.render.engine = prev_engine
    except Exception as exc:  # noqa: BLE001
        usf_log(f"AO 烘焙跳过: {exc}")


# --------------------------------------------------------------------- #
#  导出器
# --------------------------------------------------------------------- #
def export_fbx(objs, path):
    for o in bpy.data.objects:
        o.select_set(o in objs)
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        path_mode="COPY",          # 纹理随包拷贝
        embed_textures=True,        # 嵌入 FBX
        add_leaf_bones=False,
        mesh_smooth_type="OFF",
        axis_forward="-Z", axis_up="Y",
        apply_scale_options="FBX_SCALE_NONE",
    )
    usf_file(path)


def _gltf_vertex_color_kwarg():
    """按 Blender 版本返回顶点色导出参数。

    5.x: export_vertex_color 枚举 ('MATERIAL') / 4.1+: 布尔 / 旧版: export_colors。
    """
    try:
        props = bpy.ops.export_scene.gltf.get_rna_type().properties
    except (AttributeError, RuntimeError):
        return {}
    if "export_vertex_color" in props:
        p = props["export_vertex_color"]
        if getattr(p, "type", "") == "ENUM":
            return {"export_vertex_color": "MATERIAL"}
        return {"export_vertex_color": True}
    if "export_colors" in props:
        return {"export_colors": True}
    return {}


def export_glb(objs, path):
    for o in bpy.data.objects:
        o.select_set(o in objs)
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=True,
        export_texcoords=True,        # 顶点色 COLOR_0 传递重建外观
        **_gltf_vertex_color_kwarg(),
    )
    usf_file(path)


def export_blend(path):
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    usf_file(path)


def _usd_export_kwargs(fmt="USDA"):
    """按 RNA 探测 usd_export 参数名(跨版本: 4.x/5.x 属性名有差异)。

    - 顶点色: export_colors (4.x) / 5.x 可能更名;
    - 选择集: use_selection / selected_objects_only;
    - 格式枚举: 4.x 叫 export_format, 5.x 属性名不同 —— 统一按
      "枚举项含 USDA 的属性" 定位, 值取 USDA(ASCII 可读)。
    """
    try:
        props = bpy.ops.wm.usd_export.get_rna_type().properties
    except (AttributeError, RuntimeError):
        return {}
    kw = {}
    for name in ("export_colors",):
        if name in props:
            kw[name] = True
    for name in ("use_selection", "selected_objects_only"):
        if name in props:
            kw[name] = True
            break
    for prop in props:
        if getattr(prop, "type", "") == "ENUM":
            items = [i.identifier for i in (prop.enum_items or [])]
            if "USDA" in items:
                kw[prop.identifier] = fmt if fmt in items else "USDA"
                break
    return kw


def export_usd(objs, path):
    """USD 导出: 现代管线通用格式, 保留顶点色。生成 .usda(ASCII 可读)。"""
    for o in bpy.data.objects:
        o.select_set(o in objs)
    bpy.ops.wm.usd_export(filepath=str(path), **_usd_export_kwargs())
    usf_file(path)


# --------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------- #
def main():
    params = load_params()
    input_path = Path(params["input"])
    out_dir = Path(params["out_dir"])
    base = params.get("base_name", "usf_scene")
    formats = [f.lower() for f in params.get("formats", ["fbx"])]
    lods = params.get("lod_ratios", [1.0])
    out_dir.mkdir(parents=True, exist_ok=True)

    usf_log(f"Blender {bpy.app.version_string} | {input_path.name}")

    usf_progress(3, "清空默认场景")
    purge_default_scene()

    usf_progress(5, "导入网格")
    import_mesh(str(input_path))
    objs = mesh_objects()
    if not objs:
        raise RuntimeError("导入后未找到 MESH 对象")

    usf_progress(20, "拓扑清理")
    for obj in objs:
        cleanup_mesh(obj)

    usf_progress(40, "UV 与材质")
    for obj in objs:
        smart_uv(obj)
        ensure_material(obj)
    if params.get("bake_texture", True) and objs:
        usf_progress(48, "纹理烘焙")
        try:
            bake_texture(objs[0], objs[0].data.materials[0],
                         out_dir / f"{base}_texture.png")
        except Exception as exc:  # noqa: BLE001
            usf_log(f"纹理烘焙失败(不阻断): {exc}")

    if params.get("bake_ao"):
        usf_progress(50, "AO 烘焙")
        bake_ao_passthrough()

    # 主级(LOD0) 与减面级逐一导出
    outputs = []
    files_out = []
    for i, ratio in enumerate(lods):
        tag = base if i == 0 else f"{base}_lod{i}"
        pct = 55 + int(35 * (i + 1) / max(len(lods), 1))
        usf_progress(pct, f"LOD{i} ({ratio:.0%})")
        lod_objs = []
        for obj in objs:
            lod_objs.append(build_lod(obj, ratio, f"{obj.name}_LOD{i}"))
        for fmt in formats:
            if fmt == "blend":
                continue                      # blend 循环外整包单独保存
            dst = out_dir / f"{tag}.{fmt}"
            try:                              # 单格式失败不截断其余产物
                if fmt == "fbx":
                    export_fbx(lod_objs, dst)
                elif fmt in ("glb", "gltf"):
                    export_glb(lod_objs, dst)
                elif fmt in ("usd", "usda", "usdc"):
                    dst = out_dir / f"{tag}.usda"
                    export_usd(lod_objs, dst)
                else:
                    continue
                files_out.append(dst)
            except Exception as exc:  # noqa: BLE001
                usf_log(f"导出 {fmt} 失败(LOD{i}): {exc}")
        outputs.extend(lod_objs)

    # .blend 单独整包保存（含所有 LOD）
    if "blend" in formats:
        usf_progress(92, "保存 .blend")
        export_blend(out_dir / f"{base}.blend")

    usf_progress(100, "完成")
    usf_log(f"产物 {len(files_out)} 个")
    print("[USF] DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 —— 无头模式必须把异常写进 stdout
        print(f"[USF] FATAL {exc}", flush=True)
        sys.exit(1)
