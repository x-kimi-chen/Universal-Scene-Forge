# -*- coding: utf-8 -*-
"""USF Cinema 4D 桥接脚本: OBJ → FBX（在 commandline.exe 的 Python 中执行）。

由 C4dBridge 用占位符替换后落盘为临时脚本执行:
    commandline.exe -script=<temp_script.py>

- 进度协议:  [USF] PROGRESS <0-100> <msg>
- 产物协议:  [USF] FILE <abs path>
- 错误协议:  [USF] ERROR <msg>

防御式设计:
- 不同版本 C4D 对 OBJ 导入入口支持不同, 依次尝试 LoadFile / LoadDocument
- 幂等守卫: 无论 C4D 以"执行整文件"还是"调用 main()"方式运行脚本,
  转换只执行一次
"""
import os
import sys
import traceback

INPUT_OBJ = r"__USF_INPUT__"
OUTPUT_FBX = r"__USF_OUTPUT__"

_RAN = False


def main():
    global _RAN
    if _RAN:
        return
    _RAN = True

    print("[USF] PROGRESS 5 loading C4D API")
    import c4d
    from c4d import documents

    if not os.path.isfile(INPUT_OBJ):
        raise RuntimeError("input OBJ missing: %s" % INPUT_OBJ)

    print("[USF] PROGRESS 20 importing OBJ")
    doc = None
    # 入口一: LoadFile 走注册的导入器（大多数版本可用）
    try:
        c4d.documents.LoadFile(INPUT_OBJ)
        doc = documents.GetActiveDocument()
    except Exception:
        doc = None
    # 入口二: LoadDocument + 显式挂到会话
    if doc is None or not doc.GetObjects():
        try:
            loaded = c4d.documents.LoadDocument(
                INPUT_OBJ,
                c4d.SCENEFILTER_OBJECTS | c4d.SCENEFILTER_MATERIALS)
            if loaded is not None:
                documents.InsertBaseDocument(loaded)
                doc = loaded
        except Exception:
            doc = doc if (doc and doc.GetObjects()) else None

    if doc is None or not doc.GetObjects():
        raise RuntimeError("OBJ import failed (no objects in document)")

    print("[USF] PROGRESS 55 exporting FBX")
    fbx_format = getattr(c4d, "FORMAT_FBX_EXPORT", None)
    if fbx_format is None:
        fbx_format = 1026370  # FBX 导出器插件 ID（旧版本常量缺失时兜底）

    ok = c4d.documents.SaveDocument(
        doc, OUTPUT_FBX, c4d.SAVEDOCUMENTFLAGS_0, fbx_format)

    if not ok or not os.path.isfile(OUTPUT_FBX):
        raise RuntimeError(
            "FBX export failed: SaveDocument=%r exists=%r"
            % (ok, os.path.isfile(OUTPUT_FBX)))

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
