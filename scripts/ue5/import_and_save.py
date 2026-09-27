"""Universal Scene Forge — UE5 资产导入脚本（在 UnrealEditor 内部执行）。

由 app/dcc/ue5_bridge.py 以如下方式调用:
    UnrealEditor-Cmd.exe <Project.uproject> -run=pythonscript
        -script=import_and_save.py -stdout -unattended -nosplash

文件清单经环境变量 USF_UE5_IMPORT_LIST (JSON) 传入:
    {"files": ["D:/out/usf_scene.fbx"], "destination": "/Game/USF"}

前置条件（工程侧, 只需配置一次）:
    Edit → Plugins → 搜索并启用 "Python Editor Script Plugin"（内置, 免安装）

输出协议: [UEF] ASSET <object path> / [UEF] PROGRESS n msg / [UEF] ERROR msg
"""
import json
import os

import unreal


def uef_log(msg):
    unreal.log(f"[UEF] {msg}")


def uef_progress(percent, msg=""):
    unreal.log(f"[UEF] PROGRESS {int(percent)} {msg}")


def load_payload():
    with open(os.environ["USF_UE5_IMPORT_LIST"], "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    payload = load_payload()
    files = payload.get("files", [])
    destination = payload.get("destination", "/Game/USF")
    if not files:
        print("[UEF] ERROR 文件清单为空", flush=True)
        raise SystemExit(1)

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    imported = []
    total = len(files)

    for i, src in enumerate(files):
        task = unreal.AssetImportTask()
        task.filename = src
        task.destination_path = destination
        task.automated = True          # 不弹对话框
        task.save = True               # 导入后立即保存 .uasset
        task.replace_existing = True   # 覆盖旧预览
        task.name = os.path.splitext(os.path.basename(src))[0]
        try:
            asset_tools.import_asset_tasks([task])
            for obj in task.imported_object_paths:
                imported.append(obj)
                unreal.log(f"[UEF] ASSET {obj}")
        except Exception as exc:  # noqa: BLE001 单文件失败不阻断后续
            unreal.log_warning(f"[UEF] 导入失败 {src}: {exc}")
        uef_progress(int(100 * (i + 1) / total), f"{os.path.basename(src)}")

    # 整目录落盘（确保 .uasset 写入磁盘）
    if unreal.EditorAssetLibrary.does_directory_exist(destination):
        unreal.EditorAssetLibrary.save_directory(destination, only_if_is_dirty=True)
    uef_log(f"导入完成: {len(imported)} 资产 → {destination}")
    print("[UEF] DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[UEF] ERROR {exc}", flush=True)
        raise SystemExit(1)
