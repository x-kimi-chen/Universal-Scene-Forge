# -*- coding: utf-8 -*-
"""Blender 多版本桥接实测: 对本机每个 Blender (4.5 / 5.0 / 5.2-Steam)
跑 postprocess_mesh (导入→清理→UV→材质→LOD→导出 fbx/glb/blend)。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from app.dcc.blender_bridge import BlenderBridge  # noqa: E402

app = QCoreApplication([])

MESH = None
for _cand in (ROOT / "usf_work/mesh/usf_scene.obj",
              ROOT / "build/usf_selftest/msf_test/usf_scene_repaired.obj",
              ROOT / "build/usf_selftest/blender_seq/usf_work/dcc/usf_scene_repaired.obj"):
    if _cand.exists():
        MESH = _cand
        break
assert MESH is not None, "未找到任何可用测试网格"

BLENDERS = [
    ("4.5", r"D:\Program Files\Blender Foundation\Blender 4.5\blender.exe"),
    ("5.0", r"D:\Program Files\Blender Foundation\Blender 5.0\blender.exe"),
    ("5.2", r"d:\program files (x86)\steam\steamapps\common\Blender\blender.exe"),
]

results = {}
for tag, exe in BLENDERS:
    if not Path(exe).exists():
        print(f"[{tag}] SKIP (not installed): {exe}", flush=True)
        continue
    out = ROOT / f"build/usf_selftest/blender_{tag}"
    bridge = BlenderBridge(exe, timeout_s=600,
                           log_cb=lambda l: print(f"[{tag}] {l}", flush=True))
    try:
        files = bridge.postprocess_mesh(
            mesh_in=MESH, out_dir=out, base_name="usf_scene",
            formats=["fbx", "glb", "blend"], lod_ratios=[1.0, 0.5, 0.25])
        ok = len(files) >= 7 and all(f.exists() and f.stat().st_size > 0 for f in files)
        results[tag] = "PASS" if ok else f"WEAK ({len(files)} files)"
        print(f"[{tag}] -> {results[tag]} ({len(files)} files)", flush=True)
    except Exception as exc:  # noqa: BLE001
        results[tag] = f"FAIL: {exc}"
        print(f"[{tag}] FAIL: {exc}", flush=True)

print("BLENDER-VERSIONS:", json.dumps(results, ensure_ascii=False), flush=True)
