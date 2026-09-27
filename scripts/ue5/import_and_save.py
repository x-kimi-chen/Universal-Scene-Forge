"""Universal Scene Forge — UE5 资产导入脚本（在 UnrealEditor 内部执行）。

由 app/dcc/ue5_bridge.py 以如下方式调用:
    UnrealEditor-Cmd.exe <Project.uproject> -run=pythonscript
        -script=import_and_save.py -stdout -unattended -nosplash

文件清单经环境变量 USF_UE5_IMPORT_LIST (JSON) 传入:
    {"files": ["D:/out/usf_scene.fbx"], "destination": "/Game/USF"}

实现说明（UE 5.7 实测结论）:
- 主路径 AutomatedAssetImportData + import_assets_automated: **同步**完成,
  已实测 "Interchange import completed";
- InterchangeManager.import_asset 为异步, 且签名为
  (content_path 目标路径, source_data, ImportAssetParameters) —— 与直觉
  相反, 仅作回退;
- AssetTools.import_asset_tasks 在 Commandlet 下触碰 Slate 直接崩溃, 禁用;
- EditorAssetLibrary 的保存调用在部分 Commandlet 环境同样触发 Slate 断言,
  故保存放在最后尝试 —— 即使保存期崩溃, ASSET 行已输出、导入已发生。

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


def import_one_automated(src, destination):
    """同步自动导入（主路径, 已实测完成）。返回资产对象路径列表。"""
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    data = unreal.AutomatedAssetImportData()
    data.filenames = [src]
    data.destination_path = destination
    assets = asset_tools.import_assets_automated(data)
    return [str(a.get_path_name()) for a in (assets or []) if a]


def import_one_interchange(src, destination):
    """回退路径: InterchangeManager.import_asset（异步分发）。"""
    icm = unreal.InterchangeManager.get_interchange_manager_scripted()
    src_data = unreal.InterchangeSourceData()
    src_data.filenames = [src]
    params = unreal.ImportAssetParameters()
    params.destination_name = destination
    params.is_automated = True
    params.replace_existing = True
    icm.import_asset(destination, src_data, params)
    return []


def save_all(destination):
    """保存脏包: EditorAssetLibrary 在部分 Commandlet 环境触发 Slate 断言,
    因此保存放在导入全部完成之后, 且交给桥接按 ASSET 行判定成功。"""
    try:
        if unreal.EditorAssetLibrary.does_directory_exist(destination):
            unreal.EditorAssetLibrary.save_directory(
                destination, only_if_is_dirty=True)
            uef_log(f"已保存 {destination}")
            return
    except Exception as exc:  # noqa: BLE001 —— 原生崩溃无法捕获, 走不到这里
        unreal.log_warning(f"[UEF] save_directory 异常: {exc}")
    try:
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(False, True)
        uef_log("已保存脏包 (save_dirty_packages)")
    except Exception as exc:  # noqa: BLE001
        unreal.log_warning(f"[UEF] 保存失败: {exc}")


def main():
    payload = load_payload()
    files = payload.get("files", [])
    destination = payload.get("destination", "/Game/USF")
    if not files:
        print("[UEF] ERROR 文件清单为空", flush=True)
        raise SystemExit(1)

    imported = []
    total = len(files)

    for i, src in enumerate(files):
        name = os.path.basename(src)
        paths = []
        try:
            paths = import_one_automated(src, destination)
        except Exception as exc:  # noqa: BLE001
            unreal.log_warning(f"[UEF] 自动导入失败 {name}: {exc}")
        if not paths:
            try:
                paths = import_one_interchange(src, destination)
            except Exception as exc:  # noqa: BLE001 单文件失败不阻断后续
                unreal.log_warning(f"[UEF] Interchange 导入失败 {src}: {exc}")
        imported.extend(paths)
        for p in paths:
            unreal.log(f"[UEF] ASSET {p}")
        uef_progress(int(100 * (i + 1) / total), name)

    uef_log(f"导入完成: {len(imported)} 资产 → {destination}")
    save_all(destination)
    print("[UEF] DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[UEF] ERROR {exc}", flush=True)
        raise SystemExit(1)
