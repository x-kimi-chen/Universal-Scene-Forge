# -*- coding: utf-8 -*-
"""复现用户实战场景: MESH 阶段失败(如 open3d 缺失) + Metashape 修复启用 →
兜底链生效: 修复网格 → 导出成功。校验最终步骤条状态与产物落盘。"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 临时降迭代, 加速模拟(结束由外层脚本恢复 30000)
import json
sp = ROOT / "settings.json"
orig = json.loads(sp.read_text(encoding="utf-8"))
orig["gs_iterations"] = 3000
sp.write_text(json.dumps(orig, ensure_ascii=False, indent=2), encoding="utf-8")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from app.views.main_window import MainWindow  # noqa: E402

# 模拟网格阶段失败(等价于 open3d 缺失/有效高斯过少)
import app.controllers.pipeline_controller as pc  # noqa: E402


class _BrokenExtractor:
    def __init__(self, *a, **k):
        pass

    def run(self, *a, **k):
        raise RuntimeError("模拟: 泼溅网格重建失败")


pc.SplatMeshExtractor = _BrokenExtractor

win = MainWindow()
win.cb_repair.setChecked(True)          # 用户勾选了 Metashape 修复
win.ed_export_dir.setText(str(ROOT / "build/usf_selftest/scenario_out"))
win._add_source(str(ROOT / "build/usf_selftest/blender_seq"))
win._start()

deadline = time.time() + 600
while win.worker and win.worker.isRunning() and time.time() < deadline:
    app.processEvents()
    time.sleep(0.05)
app.processEvents()

print("---- result ----")
print("step texts:", [l.text() for l in win.step_labels])
print("msg       :", win.lbl_msg.text())
scen_out = ROOT / "build/usf_selftest/scenario_out"
files = [f.name for f in scen_out.iterdir()] if scen_out.exists() else []
print("export dir files:", len(files), sorted(files)[:6])

states = win._stage_states
checks = {
    "采集 done": states[0] == "done",
    "SfM done": states[1] == "done",
    "训练 done": states[2] == "done",
    "网格 failed(模拟)": states[3] == "failed",
    "展UV skipped(网格缺失)": states[4] == "skipped",
    "UE5 skipped": states[5] == "skipped",
    "后处理 failed(时序使然)": states[6] == "failed",
    "修复 done": states[7] == "done",
    "导出 done(兜底生效)": states[8] == "done",
    "产物>=10": len([f for f in files if f.endswith((".fbx", ".obj", ".glb", ".blend"))]) >= 10,
}
bad = [k for k, v in checks.items() if not v]
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
if win.worker and win.worker.isRunning():
    win.worker.controller.request_stop()
    win.worker.wait(10000)
win.close()

# 恢复正式迭代数
final = json.loads(sp.read_text(encoding="utf-8"))
final["gs_iterations"] = 30000
sp.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
print("settings gs_iterations restored 30000")
print("SCENARIO VERIFY " + ("DONE" if not bad else f"FAILED: {bad}"))
