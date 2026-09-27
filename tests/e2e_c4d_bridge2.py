# -*- coding: utf-8 -*-
"""端到端(重试): APPDATA 重定向后调用 Cinema 4D Commandline 完成 OBJ→FBX。"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OBJ_TEXT = """\
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

tmp = Path(tempfile.mkdtemp(prefix="usf_e2e_c4d2_"))
obj_in = tmp / "cube.obj"
obj_in.write_text(OBJ_TEXT, encoding="utf-8")
fbx_out = tmp / "cube.fbx"

# APPDATA 重定向(此前会话验证过的解法: 规避受限环境无法写 NVIDIA/Intel 缓存)
redir = tmp / "appdata"
redir.mkdir(exist_ok=True)
env = dict(os.environ)
env["APPDATA"] = str(redir)
env["LOCALAPPDATA"] = str(redir)

from app.dcc.c4d_bridge import C4dBridge
import app.dcc.c4d_bridge as cb_mod
import subprocess

# 直接复用桥的模板与协议逻辑, 但注入重定向 env
bridge = C4dBridge(r"C:\Program Files\Maxon Cinema 4D 2025\Cinema 4D.exe", timeout_s=300)

# 临时替换 Popen 以注入 env(测试钩子)
_orig_popen = subprocess.Popen
def _popen_env(*args, **kwargs):
    kwargs["env"] = env
    return _orig_popen(*args, **kwargs)
subprocess.Popen = _popen_env
try:
    result = bridge.export_fbx(obj_in, fbx_out)
    print(f"[E2E2] 成功: {result} 大小={result.stat().st_size}B")
except Exception as exc:
    print(f"[E2E2] 失败: {type(exc).__name__}: {exc}")
finally:
    subprocess.Popen = _orig_popen
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
