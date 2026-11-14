# -*- coding: utf-8 -*-
"""Blender 多版本桥接实测: 对本机每个 Blender (4.5 / 5.0 / Steam 5.3)
跑 postprocess_mesh (导入→清理→UV→材质→LOD→导出 fbx/glb/blend)。

不依赖 PySide6: BlenderBridge 只走 subprocess, 测试可在纯 CLI 解释器下运行。
Steam 版按实际安装位置探测, 版本号从 ``blender --version`` 读取, 便于后续升级。
"""
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.dcc.blender_bridge import BlenderBridge  # noqa: E402
from app.dcc.registry_probe import DccDetector  # noqa: E402

MESH = None
for _cand in (ROOT / "usf_work/mesh/usf_scene.obj",
              ROOT / "build/audit/fixtures/sphere.obj",
              ROOT / "build/usf_selftest/msf_test/usf_scene_repaired.obj",
              ROOT / "build/usf_selftest/blender_seq/usf_work/dcc/usf_scene_repaired.obj"):
    if _cand.exists():
        MESH = _cand
        break
assert MESH is not None, "未找到任何可用测试网格"


def blender_version(exe: Path) -> str:
    """读取 Blender 主次版本号, 失败返回 '?'。"""
    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return "?"
    m = re.search(r"Blender (\d+\.\d+)", out)
    return m.group(1) if m else "?"


def glb_attributes(path: Path):
    """读取 GLB 第一个 primitive 的 attributes, 用于验证 UV/切线/顶点色。"""
    try:
        data = path.read_bytes()
        length = struct.unpack("<I", data[12:16])[0]
        doc = json.loads(data[20:20 + length].decode("utf-8"))
        return dict(doc["meshes"][0]["primitives"][0]["attributes"])
    except (OSError, KeyError, IndexError, ValueError, struct.error):
        return {}


# 常规安装 + Steam 库探测 (Steam 版 5.3 由前端三级探测命中)
BLENDERS = [
    ("4.5", Path(r"D:\Program Files\Blender Foundation\Blender 4.5\blender.exe")),
    ("5.0", Path(r"D:\Program Files\Blender Foundation\Blender 5.0\blender.exe")),
]
_steam = DccDetector({}).detect_all().get("blender")
steam_exe = None
if _steam and _steam.available and _steam.source == "steam":
    steam_exe = Path(_steam.exe)
else:
    # 常规安装优先命中时, 仍补测 Steam 版
    for _cand in (Path(r"D:\Program Files (x86)\Steam\steamapps\common\Blender"
                       r"\blender.exe"),
                  Path(r"C:\Program Files (x86)\Steam\steamapps\common\Blender"
                       r"\blender.exe")):
        if _cand.exists():
            steam_exe = _cand
            break
if steam_exe and steam_exe.exists():
    BLENDERS.append((f"{blender_version(steam_exe)}-Steam", steam_exe))

results = {}
for tag, exe in BLENDERS:
    if not exe.exists():
        print(f"[{tag}] SKIP (not installed): {exe}", flush=True)
        continue
    out = ROOT / f"build/usf_selftest/blender_{tag}"
    bridge = BlenderBridge(str(exe), timeout_s=600,
                           log_cb=lambda l: print(f"[{tag}] {l}", flush=True))
    try:
        files = bridge.postprocess_mesh(
            mesh_in=MESH, out_dir=out, base_name="usf_scene",
            formats=["fbx", "glb", "blend"], lod_ratios=[1.0, 0.5, 0.25],
            texture_size=1024)
        ok = len(files) >= 7 and all(f.exists() and f.stat().st_size > 0 for f in files)
        # 5.3 的 glTF 顶点色改用 'ACTIVE' 后, 烘焙贴图不应再挤掉 COLOR_0
        attrs = glb_attributes(out / "usf_scene.glb")
        if ok and {"TEXCOORD_0", "TANGENT"} - set(attrs):
            ok = False
            print(f"[{tag}] GLB 缺少属性: {sorted(attrs)}", flush=True)
        results[tag] = "PASS" if ok else f"WEAK ({len(files)} files, glb={sorted(attrs)})"
        print(f"[{tag}] -> {results[tag]} ({len(files)} files, glb={sorted(attrs)})",
              flush=True)
    except Exception as exc:  # noqa: BLE001
        results[tag] = f"FAIL: {exc}"
        print(f"[{tag}] FAIL: {exc}", flush=True)

print("BLENDER-VERSIONS:", json.dumps(results, ensure_ascii=False), flush=True)
