# -*- coding: utf-8 -*-
"""USF Houdini 桥接脚本: OBJ → FBX（在 hython 中执行）。

由 HoudiniBridge 用占位符替换后落盘为临时脚本执行:
    hython.exe <temp_script.py>

- 进度协议:  [USF] PROGRESS <0-100> <msg>
- 产物协议:  [USF] FILE <abs path>
- 错误协议:  [USF] ERROR <msg>

防御式设计: FBX 导出 ROP 的节点类型名/参数名跨 Houdini 版本有差异,
运行时枚举候选并逐个尝试, 失败时输出明确错误。
"""
import os
import sys
import traceback

INPUT_OBJ = r"__USF_INPUT__"
OUTPUT_FBX = r"__USF_OUTPUT__"


def _find_fbx_rop_names(hou):
    """枚举 OBJ 上下文中可用的 FBX 导出 ROP 类型名（跨版本兼容）。"""
    names = []
    try:
        types = hou.objNodeTypeCategory().nodeTypes()
    except Exception:
        return names
    for candidate in ("rop_filmbox", "rop_filmbox2", "filmboxfbx", "rop_fbx"):
        try:
            if candidate in types:
                names.append(candidate)
        except Exception:
            continue
    return names


def _set_first_parm(node, names, value):
    """按候选顺序设置第一个存在的参数, 返回是否成功。"""
    for name in names:
        try:
            parm = node.parm(name)
            if parm is not None:
                parm.set(value)
                return True
        except Exception:
            continue
    return False


def main():
    print("[USF] PROGRESS 5 loading Houdini Python modules")
    import hou

    if not os.path.isfile(INPUT_OBJ):
        raise RuntimeError("input OBJ missing: %s" % INPUT_OBJ)

    print("[USF] PROGRESS 20 importing OBJ")
    obj_ctx = hou.node("/obj")
    geo = obj_ctx.createNode("geo", "usf_mesh", run_init_scripts=False)
    loader = geo.createNode("file", "usf_obj")
    loader.parm("file").set(INPUT_OBJ)
    loader.cook(force=True)

    print("[USF] PROGRESS 45 locating FBX export ROP")
    rop_names = _find_fbx_rop_names(hou)
    if not rop_names:
        raise RuntimeError(
            "no FBX export ROP node type found "
            "(tried: rop_filmbox, rop_filmbox2, filmboxfbx, rop_fbx)")
    rop = obj_ctx.createNode(rop_names[0], "usf_fbx")

    if not _set_first_parm(rop, ("soppath", "sop_path", "path"), geo.path()):
        raise RuntimeError("ROP has no SOP path parameter: %s" % rop_names[0])
    if not _set_first_parm(rop, ("file", "foutput", "output", "filename"),
                           OUTPUT_FBX):
        raise RuntimeError("ROP has no output file parameter: %s" % rop_names[0])

    print("[USF] PROGRESS 60 rendering FBX ROP (%s)" % rop_names[0])
    rop.render()

    if not os.path.isfile(OUTPUT_FBX):
        raise RuntimeError("FBX ROP finished but output missing: %s" % OUTPUT_FBX)
    print("[USF] FILE %s" % OUTPUT_FBX)
    print("[USF] DONE")


try:
    main()
except SystemExit:
    raise
except BaseException:
    print("[USF] ERROR %s"
          % traceback.format_exc(limit=3).replace("\n", " | "))
    sys.exit(1)
