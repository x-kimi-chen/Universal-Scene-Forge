"""Universal Scene Forge — Agisoft Metashape 内部修复脚本（核心桥接段）。

由 app/dcc/metashape_bridge.py 无头调用:
    metashape.exe -r repair_mesh.py
参数文件路径经环境变量 USF_METASHAPE_PARAMS (JSON) 传入:
{
  "frames_dir":  "D:/work/frames",       # 重建用原始帧
  "mesh_out":    "D:/output/usf_scene_repaired.obj",
  "face_count":  200000,
  "with_texture": true
}

职责（摄影测量补全）:
    对齐相机 → 深度图 → 稠密点云 → 建模(尽量内插补洞) → 洞穴修复 → 导出 OBJ

输出协议: [MSF] PROGRESS n msg / [MSF] ERROR msg
注意:
- 需要 Metashape **Professional** 授权（Standard 无脚本 API）。
- 已在 Metashape 2.3.1 实测: 2.x 移除顶层 accuracy/quality 枚举与
  buildDenseCloud, 参数更名 (surface_type/source_data/filter_mode/
  save_texture)。且对未知/None 参数**不抛错而是静默取空** —— 因此
  不能靠"先调旧签名等异常"做兼容, 必须先探测签名再调用。本脚本以
  方法文档字符串为准选择新旧两套调用, 不解析版本号。
"""
import glob
import json
import os
import sys
from pathlib import Path

import Metashape

# 顶层常量在 2.x 的容器归属（const() 依次查找）
_ENUM_CONTAINERS = ("Accuracy", "Quality", "FilterMode", "SurfaceType",
                    "DataSource", "Interpolation", "FaceCount")


def msf_log(msg):
    print(f"[MSF] {msg}", flush=True)


def msf_progress(percent, msg=""):
    print(f"[MSF] PROGRESS {int(percent)} {msg}", flush=True)


def load_params():
    path = os.environ["USF_METASHAPE_PARAMS"]
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def const(name):
    """解析 Metashape 常量: 1.x 顶层 → 2.x 子枚举 → None（调用方跳过）。"""
    if hasattr(Metashape, name):
        return getattr(Metashape, name)
    for c in _ENUM_CONTAINERS:
        cont = getattr(Metashape, c, None)
        if cont is not None and hasattr(cont, name):
            return getattr(cont, name)
    return None


def signature_of(method):
    """方法签名字符串（编译模块无 inspect, 文档首行即签名; 缺失时为空）。"""
    return (getattr(method, "__doc__", None) or "").strip()


def collect_frames(frames_dir):
    frames = []
    for pattern in ("*.jpg", "*.jpeg", "*.png", "*.tif"):
        frames.extend(glob.glob(os.path.join(frames_dir, pattern)))
    return sorted(frames)


def main():
    params = load_params()
    frames = collect_frames(params["frames_dir"])
    if len(frames) < 4:
        print(f"[MSF] ERROR 帧数不足 ({len(frames)} < 4), 无法摄影测量", flush=True)
        sys.exit(1)

    doc = Metashape.Document()
    chunk = doc.addChunk()
    chunk.addPhotos(frames)
    msf_progress(5, f"载入 {len(frames)} 帧")

    # 1) 对齐相机（2.x: downscale 整数; 1.x: accuracy 枚举）
    msf_progress(10, "特征匹配与对齐")
    if "downscale=" in signature_of(chunk.matchPhotos):
        chunk.matchPhotos(downscale=1,
                          generic_preselection=True,
                          reference_preselection=True)
    else:
        chunk.matchPhotos(accuracy=const("HighAccuracy"),
                          generic_preselection=True,
                          reference_preselection=True)
    chunk.alignCameras()
    n_aligned = sum(1 for c in chunk.cameras if c.transform)
    msf_progress(30, f"已对齐 {n_aligned}/{len(chunk.cameras)} 相机")
    if n_aligned < 4:
        print("[MSF] ERROR 对齐相机过少, 请检查帧间重叠度", flush=True)
        sys.exit(1)

    # 2) 深度图（2.x: downscale+filter_mode; 1.x: quality+filter）
    msf_progress(40, "生成深度图")
    sig = signature_of(chunk.buildDepthMaps)
    if "downscale=" in sig:
        if "filter_mode=" in sig and const("AggressiveFiltering") is not None:
            chunk.buildDepthMaps(downscale=1,
                                 filter_mode=const("AggressiveFiltering"))
        else:
            chunk.buildDepthMaps(downscale=1)
    else:
        chunk.buildDepthMaps(quality=const("UltraQuality"),
                             filter=const("AggressiveFiltering"))

    # 3) 稠密点云（可选步骤, 失败不阻断: 2.x 的 buildModel 默认
    #    source_data=DepthMapsData, 可直接从深度图建模）
    msf_progress(60, "稠密点云")
    if hasattr(chunk, "buildDenseCloud"):
        try:
            chunk.buildDenseCloud(point_confidence=True)
        except Exception as exc:  # noqa: BLE001
            msf_log(f"稠密云（旧 API）跳过: {exc}")
    elif hasattr(chunk, "buildPointCloud"):
        try:
            chunk.buildPointCloud(point_confidence=True, max_neighbors=100)
        except Exception as exc:  # noqa: BLE001
            msf_log(f"稠密点云跳过: {exc}")
    else:
        msf_log("当前版本无稠密云 API, 建模直接使用深度图")

    # 4) 建模（interpolation=Enabled 尽量填补空洞; 2.x 参数名
    #    surface_type/source_data, 1.x 为 surface/source —— 以签名为准,
    #    绝不传 None: 2.x 对 None 参数静默取空, 会导致建不出模型）
    msf_progress(75, "重建网格")
    sig = signature_of(chunk.buildModel)
    if "surface_type=" in sig:
        chunk.buildModel(surface_type=const("Arbitrary"),
                         interpolation=const("EnabledInterpolation"),
                         face_count=const("HighFaceCount"))
    elif "surface=" in sig:
        chunk.buildModel(surface=const("Arbitrary"),
                         source=const("DenseCloudData"),
                         interpolation=const("EnabledInterpolation"),
                         face_count=const("HighFaceCount"))
    else:
        chunk.buildModel()
    if chunk.model is None:
        print("[MSF] ERROR 网格建模未产出模型, 请确认数据源与授权", flush=True)
        sys.exit(1)

    # 5) 洞穴修复（版本差异: 部分版本无 Model.closeHoles）
    msf_progress(85, "洞穴修复")
    close = getattr(chunk.model, "closeHoles", None)
    if callable(close):
        try:
            close()
        except TypeError:
            close(hole_size=100)
    else:
        msf_log("当前版本无 closeHoles API, 已由 interpolation=Enabled 兜底")

    # 6) 减面 + 导出
    msf_progress(92, "减面与导出")
    face_count = int(params.get("face_count", 200_000))
    decimate = getattr(chunk, "decimateModel", None)
    if callable(decimate):
        try:
            decimate(face_count=face_count)
        except TypeError:
            decimate(max_face_count=face_count)

    mesh_out = str(Path(params["mesh_out"]))
    Path(mesh_out).parent.mkdir(parents=True, exist_ok=True)
    with_texture = bool(params.get("with_texture", True))

    # 2.3.1 实测: decimateModel 之后 chunk.model 的资产 key 变为 None,
    # 默认导出源失效报 "Null model"; 显式传 model=[chunk.model] 可正常导出。
    # 该参数 1.x 不存在, 以签名为准（"[model]" 为可选列表参数记法）。
    sig = signature_of(chunk.exportModel)
    kw = dict(path=mesh_out, binary=True)
    if "save_texture=" in sig:
        kw["save_texture"] = with_texture
    else:
        kw["texture"] = with_texture
    if hasattr(Metashape, "ModelFormat") and "[format]" not in sig:
        obj_fmt = getattr(Metashape.ModelFormat, "ModelFormatOBJ", None)
        if obj_fmt is not None and mesh_out.lower().endswith(".obj"):
            kw["format"] = obj_fmt
    try:
        if "[model]" in sig:
            kw["model"] = [chunk.model]
        chunk.exportModel(**kw)
    except Exception:  # noqa: BLE001 —— 兜底: 裸调按扩展名自动识别格式
        chunk.exportModel(path=mesh_out, binary=True,
                          **({"save_texture": with_texture}
                             if "save_texture=" in sig
                             else {"texture": with_texture}))
    msf_progress(100, f"已导出 {mesh_out}")

    # 顺带保存工程, 便于人工继续编辑
    psx = Path(mesh_out).with_suffix(".psx")
    doc.save(str(psx))
    msf_log(f"工程已保存: {psx}")
    print("[MSF] DONE", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[MSF] ERROR {exc}", flush=True)
        sys.exit(1)
