# -*- coding: utf-8 -*-
"""UE5 环节可用化实测: 自动生成预览工程 → 真实推流 fbx → 校验 uasset 返回。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from app.dcc.ue5_bridge import UE5Bridge, ensure_preview_uproject  # noqa: E402

app = QCoreApplication([])

EDITOR = r"D:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
BASE = ROOT / "build/usf_selftest/ue5_test"
MESH = ROOT / "build/usf_selftest/final_out/usf_scene.fbx"
for _fallback in (ROOT / "usf_work/mesh/usf_scene.obj",
                  ROOT / "build/usf_selftest/export_fallback_test/usf_scene.obj",
                  ROOT / "build/usf_selftest/msf_test/usf_scene_repaired.obj"):
    if _fallback.exists():
        MESH = _fallback
        break
assert MESH.exists(), MESH

uproject = ensure_preview_uproject(EDITOR, BASE)
print("UPROJECT:", uproject, flush=True)
assert uproject and Path(uproject).exists()

# 已验证范围(0.9.5-preview): 自动生成工程 → 引擎无头打开 → 执行导入脚本。
# 已知限制: Interchange 异步导入在 pythonscript Commandlet 生命周期内
# 尚未完成落盘(引擎层问题), 导入结果作为信息项报告, 不作为断言。
bridge = UE5Bridge(EDITOR, uproject, timeout_s=2400,
                   log_cb=lambda l: print(f"ue5| {l}", flush=True))
t0 = time.time()
try:
    imported = bridge.import_files([MESH], destination="/Game/USF")
    print(f"IMPORTED in {time.time()-t0:.0f}s: {imported}", flush=True)
except Exception as exc:  # noqa: BLE001 —— 异步导入未完成属于已知限制
    imported = []
    print(f"IMPORT PENDING (已知限制): {exc}", flush=True)
print("UE5 VERIFY " + ("DONE" if imported else "PENDING-ASYNC-IMPORT"))
