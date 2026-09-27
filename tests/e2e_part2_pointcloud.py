"""E2E Part2: 合成高斯点云注入 → MESH → Blender 后处理 → 多格式导出 全链路。

模拟训练已产出 point_cloud.ply（真实 3DGS PLY 属性布局），
其余阶段全部真实执行:
    INGEST (真实图像序列) → SFM (COLMAP 缺席, 容错跳过)
    → TRAIN (注入) → MESH (真实 Open3D 泊松重建)
    → UE5 (跳过) → DCC_POST (真实 Steam Blender 无头调用)
    → EXPORT (真实 Blender + OBJ 直写 + 降级链)
判定: 全部断言通过 → 退出码 0。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SH_C0 = 0.28209479177387814
WORK_DIR: Path = ROOT / "build" / "e2e_part2"      # 固定目录, 供 Part3 复用产物


# --------------------------------------------------------------------- #
#  合成数据
# --------------------------------------------------------------------- #
def make_frames(frames_dir: Path, n: int = 12, w: int = 640, h: int = 480) -> None:
    """合成图像序列（供 INGEST.collect 真实收集 + cv2 预处理）。"""
    import cv2
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        frame = np.zeros((h, w, 3), np.uint8)
        frame[:, :, 0] = int(255 * i / n)
        x = int((i / n) * (w - 120))
        frame[h // 3:h // 3 + 100, x:x + 100] = (60, 200, 60)
        y = int((1 - i / n) * (h - 80))
        frame[y:y + 80, w // 2:w // 2 + 80] = (200, 60, 60)
        cv2.imwrite(str(frames_dir / f"frame_{i:05d}.jpg"), frame)


def make_synth_gaussian_ply(ply_path: Path, n: int = 16_000) -> None:
    """合成 3DGS point_cloud.ply: 环面 R=1.2 r=0.45, 完整属性布局。

    x y z | nx ny nz | f_dc_0..2 | opacity | scale_0..2 | rot_0..3
    f_dc 由位置调色反解, opacity 设为高置信 (sigmoid≈0.9)。
    """
    from plyfile import PlyData, PlyElement

    u = np.random.default_rng(42).random(n) * 2 * np.pi
    v = np.random.default_rng(43).random(n) * 2 * np.pi
    R, r = 1.2, 0.45
    x = (R + r * np.cos(v)) * np.cos(u)
    y = r * np.sin(v)
    z = (R + r * np.cos(v)) * np.sin(u)

    # 环面外法线
    nx = np.cos(v) * np.cos(u)
    ny = np.sin(v)
    nz = np.cos(v) * np.sin(u)

    # 位置调色 (环向渐变 + 高度染色), f_dc = (rgb - 0.5)/SH_C0
    rgb = np.stack([0.5 + 0.45 * np.sin(u),
                    0.5 + 0.30 * np.sin(2 * v),
                    0.5 + 0.40 * np.cos(u)], axis=1)
    f_dc = (rgb - 0.5) / SH_C0

    opacity = np.full(n, 2.2)                       # sigmoid(2.2) ≈ 0.90
    scale = np.random.default_rng(44).uniform(
        np.log(0.01), np.log(0.03), size=(n, 3))    # 高斯尺度 ~1-3cm
    rot = np.zeros((n, 4)); rot[:, 0] = 1.0         # 单位四元数

    arr = np.zeros(n, dtype=[(k, "f4") for k in (
        "x", "y", "z", "nx", "ny", "nz",
        "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
        "scale_0", "scale_1", "scale_2",
        "rot_0", "rot_1", "rot_2", "rot_3")])
    for name, col in (("x", x), ("y", y), ("z", z), ("nx", nx), ("ny", ny),
                      ("nz", nz)):
        arr[name] = col
    arr["f_dc_0"], arr["f_dc_1"], arr["f_dc_2"] = f_dc.T
    arr["opacity"] = opacity
    arr["scale_0"], arr["scale_1"], arr["scale_2"] = scale.T
    arr["rot_0"], arr["rot_1"], arr["rot_2"], arr["rot_3"] = rot.T

    ply_path.parent.mkdir(parents=True, exist_ok=True)
    PlyData([PlyElement.describe(arr, "vertex")], text=False).write(str(ply_path))
    print(f"[E2E2] 合成高斯 PLY: {ply_path} ({n} 点, {ply_path.stat().st_size//1024} KB)")


# --------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------- #
def main() -> int:
    from app.utils.logger import setup_logging
    from app.controllers.pipeline_controller import EventBus, PipelineController
    from app.models.settings import PipelineSettings
    from app.models.project import Stage

    setup_logging()
    import shutil
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    frames_dir = WORK_DIR / "input_frames"
    make_frames(frames_dir)
    ply = WORK_DIR / "model" / "point_cloud" / "iteration_7000" / "point_cloud.ply"
    make_synth_gaussian_ply(ply)

    settings = PipelineSettings()
    bus = EventBus()
    ctrl = PipelineController(settings, bus)

    # 注入 TRAIN: 模拟官方仓库已产出 7000 迭代点云
    ctrl._train = lambda work_dir: (ctrl.state.register(Stage.TRAIN.value, [ply]), ply)[1]

    ok = ctrl.execute(frames_dir, WORK_DIR)

    by_name = {r.name: r for r in ctrl.state.records}
    checks: list = []

    def check(cond, msg):
        checks.append((bool(cond), msg))
        print(f"  {'PASS' if cond else 'FAIL'}  {msg}")

    print("\n[E2E2] 阶段结果:")
    for r in ctrl.state.records:
        status = "OK" if r.ok else ("SKIP" if r.skipped else "FAIL")
        print(f"  {r.name:12s} {status:4s} {r.elapsed_s:6.1f}s {r.message[:60]}")

    mesh_dir = WORK_DIR / "mesh"
    dcc_dir = WORK_DIR / "dcc"
    out_dir = WORK_DIR / "output"

    print("\n[E2E2] 断言:")
    check(by_name.get("输入采集与抽帧") and by_name["输入采集与抽帧"].ok,
          "INGEST 图像序列收集成功")
    check("COLMAP 位姿求解" in ctrl.failed_stages, "SFM 容错跳过 (COLMAP 缺席, 预期)")
    check(by_name.get("泼溅网格重建") and by_name["泼溅网格重建"].ok,
          "MESH 泊松重建成功 (真实 Open3D)")
    check((mesh_dir / "usf_scene.obj").exists(),
          "主网格 OBJ 已产出")
    lods = sorted(mesh_dir.glob("usf_scene_lod*.obj"))
    check(len(lods) >= 2, f"LOD 网格已产出 ({len(lods)} 级)")

    check(by_name.get("Blender 网格后处理") and by_name["Blender 网格后处理"].ok,
          "DCC_POST Steam Blender 真实调用成功")
    check((dcc_dir / "usf_scene.fbx").exists(), "Blender FBX 已产出")
    check((dcc_dir / "usf_scene.glb").exists(), "Blender GLB 已产出")
    check((dcc_dir / "usf_scene.blend").exists(), "Blender BLEND 已产出")

    check(by_name.get("多格式导出") and by_name["多格式导出"].ok,
          "EXPORT 多格式导出成功")
    check((out_dir / "usf_scene.fbx").exists(), "导出 FBX 存在")
    check((out_dir / "usf_scene.obj").exists(), "导出 OBJ 存在 (Open3D 直写路径)")
    check((out_dir / "usf_scene.glb").exists(), "导出 GLB 存在")
    check((out_dir / "usf_scene.blend").exists(), "导出 BLEND 存在")
    check(len(list(out_dir.glob("usf_scene_lod*.obj"))) >= 2, "导出 OBJ LOD 存在")
    check(not ok or "COLMAP 位姿求解" in ctrl.failed_stages,
          "整体结论与阶段记录自洽")

    n_pass = sum(1 for c, _ in checks if c)
    print(f"\n[E2E2] 结果: {n_pass}/{len(checks)} 断言通过 | 工作目录: {WORK_DIR}")
    return 0 if n_pass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
