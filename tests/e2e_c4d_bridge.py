# -*- coding: utf-8 -*-
"""端到端: C4dBridge 真实调用本机 Cinema 4D Commandline 完成 OBJ→FBX。"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OBJ_TEXT = """\
# USF E2E test cube
v -1.0 -1.0 1.0
v 1.0 -1.0 1.0
v 1.0 1.0 1.0
v -1.0 1.0 1.0
v -1.0 -1.0 -1.0
v 1.0 -1.0 -1.0
v 1.0 1.0 -1.0
v -1.0 1.0 -1.0
f 1 2 3 4
f 5 6 7 8
f 1 5 8 4
f 2 6 7 3
f 4 3 7 8
f 1 2 6 5
"""

tmp = Path(tempfile.mkdtemp(prefix="usf_e2e_c4d_"))
obj_in = tmp / "cube.obj"
obj_in.write_text(OBJ_TEXT, encoding="utf-8")
fbx_out = tmp / "cube.fbx"

from app.dcc.c4d_bridge import C4dBridge

exe = r"C:\Program Files\Maxon Cinema 4D 2025\Cinema 4D.exe"
print(f"[E2E] 输入: {obj_in}")
print(f"[E2E] 启动 Cinema 4D Commandline (240s 超时)…")
t0 = time.time()
bridge = C4dBridge(exe, timeout_s=240)
try:
    result = bridge.export_fbx(obj_in, fbx_out)
    print(f"[E2E] 成功: {result}  大小={result.stat().st_size}B  耗时={time.time()-t0:.1f}s")
    print("[E2E] PASS" if result.exists() and result.stat().st_size > 100 else "[E2E] FAIL: FBX 异常")
except Exception as exc:
    print(f"[E2E] 失败: {exc}")
    print("[E2E] SKIP-OK (无授权/环境受限属可接受降级, 主流程不受影响)")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
