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
import bmesh


# --------------------------------------------------------------------- #
#  输出协议
# --------------------------------------------------------------------- #
def usf_log(msg):
    print(f"[USF] {msg}", flush=True)


def usf_progress(percent, msg=""):
    print(f"[USF] PROGRESS {int(percent)} {msg}", flush=True)


def usf_file(path):
    print(f"[USF] FILE {Path(path).as_posix()}", flush=True)


class MeshValidationError(RuntimeError):
    pass


def verify_output(path):
    """产物必须真实存在且非零字节, 否则视为导出失败（不谎报成功）。"""
    p = Path(path)
    if not p.is_file() or p.stat().st_size <= 0:
        raise RuntimeError(f"导出产物缺失或为 0 字节: {p}")
    usf_log(f"产物校验通过: {p.name} ({p.stat().st_size} 字节)")


def safe_base_name(name):
    """base_name 只允许文件名, 防止 ../ 或盘符逃逸出 out_dir。"""
    text = str(name or "usf_scene").strip()
    if (not text or text in (".", "..")
            or any(ch in text for ch in '\\/:*?"<>|\r\n')):
        raise RuntimeError(f"非法 base_name（含路径分隔符/保留字符）: {text!r}")
    return text


def load_params():
    argv = sys.argv[sys.argv.index("--") + 1:]
    if not argv:
        raise RuntimeError("缺少参数文件: 用法 blender --python mesh_to_fbx.py -- params.json")
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
                selected_editable_objects=[obj],
                active_object=obj,
                active_objects=[obj]):
            return fn()
    except (AttributeError, TypeError):
        return fn()


# --------------------------------------------------------------------- #
#  导出状态管理（验证连续多格式导出是否存在选择/变换/UV 副作用）
# --------------------------------------------------------------------- #
def reset_export_state():
    """每次导出前重置: 回 OBJECT 模式 + 取消全部选择 + 清空活动对象。"""
    try:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except RuntimeError:
        pass
    for o in bpy.data.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = None


def state_snapshot(objs):
    """选择/活动对象/UV 层数/变换 的快照, 用于导出副作用对比。"""
    active = bpy.context.view_layer.objects.active
    return {
        "selected": tuple(sorted(o.name for o in bpy.data.objects if o.select_get())),
        "active": active.name if active else None,
        "uv_layers": tuple((o.name, len(o.data.uv_layers)) for o in objs),
        "transforms": tuple(
            (o.name, tuple(round(v, 6) for v in o.location),
             tuple(round(v, 6) for v in o.rotation_euler),
             tuple(round(v, 6) for v in o.scale)) for o in objs),
        "mode": bpy.context.mode,
    }


def _select_only(objs):
    if bpy.context.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
    for o in bpy.data.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def run_export(fmt, objs, dst, fn):
    """统一导出包装: 显式 context 覆盖 + 副作用检查 + 重置 + 产物校验。"""
    # 导出前显式检查 UV: 缺 UV 的网格在 FBX/OBJ/GLB/USD 中会没有纹理坐标。
    bad_uv = [o.name for o in objs if not _uv_usable(o.data)]
    if bad_uv:
        usf_log(f"警告: {fmt} 导出对象 UV 不可用: {', '.join(bad_uv)}")
    _select_only(objs)
    before = state_snapshot(objs)
    kwargs = {
        "object": objs[0] if objs else None,
        "active_object": objs[0] if objs else None,
        "selected_objects": list(objs),
        "selected_editable_objects": list(objs),
    }
    try:
        with bpy.context.temp_override(**kwargs):
            fn()
    except (AttributeError, TypeError):
        fn()
    after = state_snapshot(objs)
    changed = [k for k in before if before[k] != after[k]]
    if changed:
        usf_log(f"警告: {fmt} 导出改变了场景状态 {changed}, 已重置")
    else:
        usf_log(f"{fmt} 导出后状态一致（选择/活动对象/UV/变换无副作用）")
    reset_export_state()
    verify_output(dst)
    usf_file(dst)


# --------------------------------------------------------------------- #
#  网格边界校验
# --------------------------------------------------------------------- #
def validate_mesh(objs):
    """空网格/零面数 → 可读失败; 非流形仅告警（重建网格常见）。"""
    if not objs:
        raise MeshValidationError("导入后未找到 MESH 对象（空场景）")
    verts = sum(len(o.data.vertices) for o in objs)
    faces = sum(len(o.data.polygons) for o in objs)
    loose = 0
    nonmanifold = 0
    for o in objs:
        bm = bmesh.new()
        try:
            bm.from_mesh(o.data)
            loose += sum(1 for v in bm.verts if not v.link_edges)
            nonmanifold += sum(1 for e in bm.edges if not e.is_manifold)
        finally:
            bm.free()
    usf_log(f"网格统计: {verts} 顶点 / {faces} 面 / "
            f"松散顶点 {loose} / 非流形边 {nonmanifold}")
    if verts == 0:
        raise MeshValidationError("网格为 0 顶点（空网格），无法导出")
    if faces == 0:
        raise MeshValidationError(
            "网格为 0 面（仅点/边，无法导出实体网格）；请检查 MESH 阶段产物")
    if nonmanifold:
        usf_log(f"警告: {nonmanifold} 条非流形边, 继续导出"
                "（可能影响重拓扑/UV, 不影响文件有效性）")


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
        ".usd": [("wm", "usd_import")],
        ".usda": [("wm", "usd_import")],
        ".usdc": [("wm", "usd_import")],
    }
    if ext not in attempts:
        raise MeshValidationError(
            f"不支持的输入格式: {ext or '(无扩展名)'}"
            "（支持 obj/ply/stl/fbx/glb/gltf/usd/usda/usdc）")
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
def _uv_stats(mesh):
    """返回 (非零 UV 循环数, 总循环数, 当前 UV 层)。"""
    layer = mesh.uv_layers.active or (mesh.uv_layers[0]
                                      if mesh.uv_layers else None)
    if layer is None or not mesh.loops or len(layer.data) != len(mesh.loops):
        return 0, len(mesh.loops), layer
    vals = [tuple(item.uv) for item in layer.data]
    nonzero = sum(1 for u, v in vals if abs(u) > 1e-6 or abs(v) > 1e-6)
    return nonzero, len(vals), layer


def _uv_usable(mesh) -> bool:
    """UV 层存在且至少一半循环不是 (0,0)。空 UV 层会让导出器写出无效
    TEXCOORD/vt, 表现为“有 UV 但贴图全黑/拉伸”。"""
    nonzero, total, _ = _uv_stats(mesh)
    return total > 0 and nonzero >= max(1, int(total * 0.5))


def _layer_nonzero(layer, mesh) -> int:
    if layer is None or len(layer.data) != len(mesh.loops):
        return 0
    return sum(1 for item in layer.data
               if abs(item.uv[0]) > 1e-6 or abs(item.uv[1]) > 1e-6)


def merge_uv_layers(obj) -> None:
    """把多个 UV 层合并为一个规范 UVMap。

    交付物只应带一套纹理坐标: GLB/OBJ/FBX 多 UV 集会让下游引擎选错
    TEXCOORD, 烘焙贴图也无法同时对应多套坐标。策略:
    - 保留 active_render 层; 否则保留 active 层; 否则保留非零覆盖最高者;
    - 其余层删除（不同 UV 布局无法在没有材质映射信息时安全 atlas 合并）;
    - 统一命名为 UVMap 并设为 active_render。
    """
    mesh = obj.data
    if not mesh.loops:
        return
    layers = list(mesh.uv_layers)
    valid = [l for l in layers if _layer_nonzero(l, mesh) > 0]
    invalid = [l for l in layers if l not in valid]
    if invalid:
        for layer in invalid:
            try:
                mesh.uv_layers.remove(layer)
            except (AttributeError, RuntimeError):
                pass
    if not valid:
        return
    if len(valid) == 1:
        layer = valid[0]
        try:
            layer.active_render = True
            mesh.uv_layers.active = layer
        except (AttributeError, TypeError):
            pass
        if invalid:
            usf_log(f"UV 清理: {obj.name} 移除 {len(invalid)} 个空/退化层")
        return

    primary = next((l for l in valid if l.active_render), None)
    if primary is None:
        active = mesh.uv_layers.active
        primary = active if active in valid else None
    if primary is None:
        primary = max(valid, key=lambda l: _layer_nonzero(l, mesh))
    primary_name = primary.name
    dropped = [l.name for l in valid if l is not primary]
    coords = [tuple(item.uv) for item in primary.data]

    for layer in list(mesh.uv_layers):
        try:
            mesh.uv_layers.remove(layer)
        except (AttributeError, RuntimeError):
            pass
    merged = mesh.uv_layers.new(name="UVMap")
    for i, uv in enumerate(coords):
        if i < len(merged.data):
            merged.data[i].uv = uv
    try:
        merged.active_render = True
        mesh.uv_layers.active = merged
    except (AttributeError, TypeError):
        pass
    usf_log(
        f"UV 合并: {obj.name} 保留 {primary_name}, 删除 {len(dropped)} 层"
        f"（{', '.join(dropped)}）→ UVMap")


def smart_uv(obj):
    """确保对象有可用的 UV。已有但为空/退化的 UV 会被重建。

    旧实现只要 uv_layers 非空就 return, 从其他 DCC 导入的“空 UV 层”
    因此被原样导出, 造成纹理坐标缺失。现在先校验非零覆盖率, 再决定
    是否重做；smart_project 失败时回退 cube_project。
    """
    mesh = obj.data
    if not mesh.loops:
        return
    if _uv_usable(mesh):
        layer = mesh.uv_layers.active or mesh.uv_layers[0]
        try:
            layer.active_render = True
        except (AttributeError, TypeError):
            pass
        nonzero, total, _ = _uv_stats(mesh)
        merge_uv_layers(obj)          # 多 UV 层 → 单一规范 UVMap
        usf_log(f"UV 已存在且有效: {obj.name} ({nonzero}/{total} 非零)")
        return

    # 清掉空/退化 UV 层, 避免导出器选中死图层
    for layer in list(mesh.uv_layers):
        try:
            mesh.uv_layers.remove(layer)
        except (AttributeError, RuntimeError):
            pass
    layer = mesh.uv_layers.new(name="UVMap")
    try:
        mesh.uv_layers.active = layer
    except (AttributeError, TypeError):
        pass

    def _uv_ops():
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        try:
            bpy.ops.uv.smart_project(
                angle_limit=math.radians(66.0), island_margin=0.02,
                correct_aspect=True, scale_to_bounds=True)
        except TypeError:      # 旧版参数集
            bpy.ops.uv.smart_project(
                angle_limit=math.radians(66.0), island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")

    _with_override(obj, _uv_ops)
    if not _uv_usable(mesh):
        def _cube_ops():
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.cube_project(cube_size=2.0)
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            _with_override(obj, _cube_ops)
        except Exception as exc:  # noqa: BLE001
            usf_log(f"UV 立方投影失败: {exc}")

    merge_uv_layers(obj)              # 生成后同样归一为单一 UVMap
    nonzero, total, layer = _uv_stats(mesh)
    if layer is not None:
        try:
            layer.active_render = True
        except (AttributeError, TypeError):
            pass
    usf_log(f"UV 生成完成: {obj.name} ({nonzero}/{total} 非零)")
    if total == 0 or nonzero == 0:
        usf_log(f"警告: {obj.name} 的 UV 仍为空, 导出可能缺少纹理坐标")


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
    """把当前材质外观烘焙到 UV 贴图, 并让 BaseColor 改用该贴图。

    前置: obj 已有 UV (smart_uv)。烘焙后 FBX/GLB 交付带真实贴图,
    观感显著优于纯顶点色。open3d 顶点色链路下等价于"外观烘焙"。
    所有 bpy.ops 走 _with_override, 避免 --background 下缺少活动窗口失败。
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

        def _bake_ops():
            bpy.ops.object.mode_set(mode="OBJECT")
            set_engine("CYCLES")     # 烘焙仅 CYCLES 支持
            scene.cycles.samples = 1
            bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"},
                                use_clear=True, use_selected_to_active=False)
        _with_override(obj, _bake_ops)
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
        return True
    except Exception as exc:  # noqa: BLE001 烘焙失败回退顶点色, 不阻断
        usf_log(f"纹理烘焙跳过: {exc}")
        return False
    finally:
        set_engine(prev_engine)


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
    def _do():
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
    run_export("fbx", objs, path, _do)


def export_obj(objs, path):
    """Blender 4.0+ 使用 bpy.ops.wm.obj_export（3.x 回退 export_scene.obj）。"""
    def _do():
        if hasattr(bpy.ops.wm, "obj_export"):
            props = bpy.ops.wm.obj_export.get_rna_type().properties
            kw = {"filepath": str(path)}
            for name, value in (("export_selected_objects", True),
                                ("path_mode", "COPY"),
                                ("export_materials", True),
                                ("export_uv", True),
                                ("apply_modifiers", True)):
                if name in props:
                    kw[name] = value
            bpy.ops.wm.obj_export(**kw)
        else:
            bpy.ops.export_scene.obj(filepath=str(path), use_selection=True,
                                     path_mode="COPY", use_materials=True)
    run_export("obj", objs, path, _do)


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


def export_glb(objs, path, separate=False):
    def _do():
        props = bpy.ops.export_scene.gltf.get_rna_type().properties
        kw = {
            "filepath": str(path),
            "export_format": "GLTF_SEPARATE" if separate else "GLB",
            "use_selection": True,
            "export_texcoords": True,     # 纹理坐标
            "export_normals": True,
        }
        if "export_tangents" in props:
            kw["export_tangents"] = True  # 法线贴图/引擎所需切线
        kw.update(_gltf_vertex_color_kwarg())
        bpy.ops.export_scene.gltf(**kw)
    run_export("gltf" if separate else "glb", objs, path, _do)


def export_blend(path):
    # save_as_mainfile 不依赖选择/活动对象, 直接调用; 产物同样校验。
    # 关掉 .blend1 自动备份: 导出目录是交付物, 备份文件会被误当成产物。
    try:
        bpy.context.preferences.filepaths.save_version = 0
    except (AttributeError, TypeError):
        pass
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    verify_output(path)
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
    # 显式导出 UV/顶点色; 不同 Blender 版本属性名不同, 按 RNA 探测。
    for name in ("export_colors", "export_uvmaps"):
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
    """USD 导出: 保留顶点色; 按后缀选择 USDA(ASCII) / USDC(二进制)。"""
    fmt = "USDC" if path.suffix.lower() == ".usdc" else "USDA"
    def _do():
        bpy.ops.wm.usd_export(filepath=str(path), **_usd_export_kwargs(fmt))
    run_export("usd", objs, path, _do)


# --------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------- #
def main():
    params = load_params()
    input_path = Path(params["input"]).expanduser()
    if not input_path.is_file():
        raise RuntimeError(f"输入不存在: {input_path}")
    input_path = input_path.resolve()
    out_dir = Path(params["out_dir"]).expanduser()
    if not out_dir.is_absolute():
        raise RuntimeError(f"out_dir 必须是绝对路径: {out_dir}")
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    base = safe_base_name(params.get("base_name", "usf_scene"))
    supported = {"fbx", "obj", "glb", "gltf", "blend", "usd", "usda", "usdc"}
    formats = [f.lower() for f in params.get("formats", ["fbx"])]
    unknown = [f for f in formats if f not in supported]
    if unknown:
        usf_log(f"忽略不支持的格式: {', '.join(unknown)}")
    formats = [f for f in formats if f in supported]
    if not formats:
        raise RuntimeError("formats 为空或全部不受支持")
    try:
        lods = [max(0.001, min(1.0, float(r)))
                for r in (params.get("lod_ratios") or [1.0])]
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"lod_ratios 非法: {params.get('lod_ratios')!r}") from exc

    usf_log(f"Blender {bpy.app.version_string} | {input_path.name}")

    usf_progress(3, "清空默认场景")
    purge_default_scene()

    usf_progress(5, "导入网格")
    import_mesh(str(input_path))
    objs = mesh_objects()
    validate_mesh(objs)          # 空网格/零面数 → 可读失败（不崩溃）

    usf_progress(20, "拓扑清理")
    for obj in objs:
        cleanup_mesh(obj)
    validate_mesh(objs)          # 清理可能删除全部退化面, 再校验一次

    usf_progress(40, "UV 与材质")
    for obj in objs:
        smart_uv(obj)
        ensure_material(obj)
    if params.get("bake_texture", True) and objs:
        usf_progress(48, "纹理烘焙")
        try:
            mat = objs[0].data.materials[0] if objs[0].data.materials else None
            if mat is None:
                raise RuntimeError("材质创建失败, 无可用烘焙目标")
            bake_texture(objs[0], mat, out_dir / f"{base}_texture.png")
        except Exception as exc:  # noqa: BLE001
            usf_log(f"纹理烘焙失败(不阻断): {exc}")

    if params.get("bake_ao"):
        usf_progress(50, "AO 烘焙")
        bake_ao_passthrough()

    # 主级(LOD0) 与减面级逐一导出; 每次导出前 reset_export_state
    files_out = []
    for i, ratio in enumerate(lods):
        tag = base if i == 0 else f"{base}_lod{i}"
        pct = 55 + int(35 * (i + 1) / max(len(lods), 1))
        usf_progress(pct, f"LOD{i} ({ratio:.0%})")
        if ratio >= 0.999:
            lod_objs = list(objs)             # LOD0 直接复用原对象, 避免重复
        else:
            lod_objs = [build_lod(obj, ratio, f"{obj.name}_LOD{i}")
                        for obj in objs]
        for fmt in formats:
            if fmt == "blend":
                continue                      # blend 循环外整包单独保存
            ext = {"usd": "usda", "usda": "usda", "usdc": "usdc"}.get(fmt, fmt)
            dst = out_dir / f"{tag}.{ext}"
            try:                              # 单格式失败不截断其余产物
                if fmt == "fbx":
                    export_fbx(lod_objs, dst)
                elif fmt == "obj":
                    export_obj(lod_objs, dst)
                elif fmt == "glb":
                    export_glb(lod_objs, dst)
                elif fmt == "gltf":
                    export_glb(lod_objs, dst, separate=True)
                elif fmt.startswith("usd"):
                    export_usd(lod_objs, dst)
                else:
                    continue
                files_out.append(dst)
            except Exception as exc:  # noqa: BLE001
                usf_log(f"导出 {fmt} 失败(LOD{i}): {exc}")

    # .blend 单独整包保存（含所有 LOD）
    if "blend" in formats:
        usf_progress(92, "保存 .blend")
        try:
            blend_path = out_dir / f"{base}.blend"
            export_blend(blend_path)
            files_out.append(blend_path)
        except Exception as exc:  # noqa: BLE001
            usf_log(f"导出 blend 失败: {exc}")

    if not files_out:
        raise RuntimeError("未产出任何格式文件（检查各格式错误日志）")
    usf_progress(100, "完成")
    usf_log(f"产物 {len(files_out)} 个: "
            + ", ".join(p.name for p in files_out))
    print("[USF] DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 —— 无头模式必须把异常写进 stdout
        print(f"[USF] FATAL {exc}", flush=True)
        sys.exit(1)
