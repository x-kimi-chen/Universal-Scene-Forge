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
- API 名称在 1.7 / 1.8 / 2.x 间存在差异, 已做 TypeError 兼容回退。
"""
import glob
import json
import os
import sys
from pathlib import Path

import Metashape


def msf_log(msg):
    print(f"[MSF] {msg}", flush=True)


def msf_progress(percent, msg=""):
    print(f"[MSF] PROGRESS {int(percent)} {msg}", flush=True)


def load_params():
    path = os.environ["USF_METASHAPE_PARAMS"]
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_frames(frames_dir):
    frames = []
    for pattern in ("*.jpg", "*.jpeg", "*.png", "*.tif"):
        frames.extend(glob.glob(os.path.join(frames_dir, pattern)))
    return sorted(frames)


def call_compat(fn, primary, fallback, label):
    """按新旧两种签名依次尝试（Metashape 1.x vs 2.x 命名差异）。"""
    try:
        return fn(**primary)
    except TypeError:
        msf_log(f"{label}: 使用兼容签名 {sorted(fallback)}")
        return fn(**fallback)


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

    # 1) 对齐相机
    msf_progress(10, "特征匹配与对齐")
    call_compat(
        chunk.matchPhotos,
        dict(accuracy=Metashape.HighAccuracy,
             generic_preselection=True, reference_preselection=True),
        dict(downscale=1, generic_preselection=True,
             reference_preselection=True),
        "matchPhotos")
    chunk.alignCameras()
    n_aligned = sum(1 for c in chunk.cameras if c.transform)
    msf_progress(30, f"已对齐 {n_aligned}/{len(chunk.cameras)} 相机")
    if n_aligned < 4:
        print("[MSF] ERROR 对齐相机过少, 请检查帧间重叠度", flush=True)
        sys.exit(1)

    # 2) 深度图 + 稠密云（弱纹理区域补全的关键）
    msf_progress(40, "生成深度图")
    call_compat(
        chunk.buildDepthMaps,
        dict(quality=Metashape.UltraQuality, filter=Metashape.AggressiveFiltering),
        dict(downscale=1, filter_mode=Metashape.MildFiltering),
        "buildDepthMaps")
    msf_progress(60, "稠密点云")
    call_compat(
        chunk.buildDenseCloud,
        dict(point_confidence=True),
        dict(max_neighbors=100),
        "buildDenseCloud")

    # 3) 建模（interpolation=Enabled 尽量填补空洞）
    msf_progress(75, "重建网格")
    try:
        chunk.buildModel(surface=Metashape.Arbitrary,
                         source=Metashape.DenseCloudData,
                         interpolation=Metashape.EnabledInterpolation,
                         face_count=Metashape.HighFaceCount)
    except TypeError:
        chunk.buildModel(surface=Metashape.Arbitrary,
                         source=Metashape.DenseCloudData,
                         interpolation=Metashape.EnabledInterpolation)

    # 4) 洞穴修复（版本差异: 部分版本无 Model.closeHoles）
    msf_progress(85, "洞穴修复")
    close = getattr(chunk.model, "closeHoles", None)
    if callable(close):
        try:
            close()
        except TypeError:
            close(hole_size=100)
    else:
        msf_log("当前版本无 closeHoles API, 已由 interpolation=Enabled 兜底")

    # 5) 减面 + 导出
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
    try:
        chunk.exportModel(path=mesh_out, binary=True,
                         texture=params.get("with_texture", True))
    except TypeError:
        chunk.exportModel(path=mesh_out)
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
