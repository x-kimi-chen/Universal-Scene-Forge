"""E2E Part3: 3ds Max 2026 真实调用验证（OBJ → FBX 无头转换）。

前置: Part2 产物 build/e2e_part2/mesh/usf_scene.obj（缺失时现场合成环面 OBJ）。
链路: MaxBridge → 3dsmaxbatch.exe → obj_to_fbx.ms → [USF] 协议回传。
判定: FBX 真实产出且非空 → 退出码 0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OBJ_PATH = ROOT / "build" / "e2e_part2" / "mesh" / "usf_scene.obj"
OUT_FBX = ROOT / "build" / "e2e_part3_max" / "usf_scene_max.fbx"


def make_torus_obj(obj_path: Path, nu: int = 120, nv: int = 60,
                   R: float = 1.2, r: float = 0.45) -> None:
    """保底: 生成环面 OBJ（顶点色省略, 仅几何）。"""
    u, v = np.meshgrid(np.linspace(0, 2 * np.pi, nu, endpoint=False),
                       np.linspace(0, 2 * np.pi, nv, endpoint=False))
    x = (R + r * np.cos(v)) * np.cos(u)
    y = r * np.sin(v)
    z = (R + r * np.cos(v)) * np.sin(u)
    verts = np.stack([x, y, z], axis=-1).reshape(-1, 3)
    faces = []
    idx = lambda i, j: (i % nu) * nv + (j % nv)
    for i in range(nu):
        for j in range(nv):
            a, b, c, d = idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)
            faces.append((a, b, c))
            faces.append((a, c, d))
    obj_path.parent.mkdir(parents=True, exist_ok=True)
    with obj_path.open("w", encoding="ascii") as f:
        f.write("# USF E2E synthetic torus\n")
        for p in verts:
            f.write(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n")
        for a, b, c in faces:
            f.write(f"f {a + 1} {b + 1} {c + 1}\n")
    print(f"[E2E3] 保底环面 OBJ: {obj_path} ({len(verts)} v / {len(faces)} f)")


def main() -> int:
    from app.utils.logger import setup_logging
    from app.dcc.registry_probe import DccDetector
    from app.dcc.max_bridge import MaxBridge

    setup_logging()
    if not OBJ_PATH.exists():
        make_torus_obj(OBJ_PATH)
    else:
        print(f"[E2E3] 复用 Part2 网格: {OBJ_PATH} "
              f"({OBJ_PATH.stat().st_size // 1024} KB)")

    infos = DccDetector().detect_all()
    info = infos.get("max")
    if not info or not info.available:
        print("FAIL  未探测到 3ds Max")
        return 1
    print(f"[E2E3] 3ds Max: {info.exe} (source={info.source})")

    progress_lines: list = []

    def on_progress(pct, msg):
        progress_lines.append((pct, msg))
        print(f"  [progress] {pct}% {msg}")

    bridge = MaxBridge(info.exe, timeout_s=900, progress_cb=on_progress)
    fbx = bridge.export_fbx(OBJ_PATH, OUT_FBX)

    checks: list = []

    def check(cond, msg):
        checks.append((bool(cond), msg))
        print(f"  {'PASS' if cond else 'FAIL'}  {msg}")

    print("\n[E2E3] 断言:")
    check(fbx.exists(), f"FBX 已产出: {fbx}")
    check(fbx.stat().st_size > 10_000, f"FBX 非空 ({fbx.stat().st_size // 1024} KB)")
    check(any(p == 10 for p, _ in progress_lines), "[USF] PROGRESS 协议回传正常")
    with open(fbx, "rb") as f:
        head = f.read(64)
    check(b"Kaydara FBX Binary" in head or b"FBX" in head, "FBX 文件头合法")

    n_pass = sum(1 for c, _ in checks if c)
    print(f"\n[E2E3] 结果: {n_pass}/{len(checks)} 断言通过 | 产物: {OUT_FBX}")
    return 0 if n_pass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
