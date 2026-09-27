# -*- coding: utf-8 -*-
"""验证新进度反馈: 步骤条状态流转 / 进度条 / 失败弹窗(记录器)。"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np

tmp = Path(tempfile.mkdtemp(prefix="usf_repro_"))
vid = tmp / "clip.avi"
vw = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 64))
for k in range(12):
    vw.write(np.full((64, 64, 3), (k * 20, 80, 160), dtype=np.uint8))
vw.release()

from PySide6.QtWidgets import QApplication

app = QApplication([])
from app.views.main_window import MainWindow, STAGE_ORDER

alerts = []
win = MainWindow()
win._alert_failure = lambda title, detail: alerts.append((title, detail))
win._add_source(str(vid))
win._start()

deadline = __import__("time").time() + 90
while win.worker and win.worker.isRunning() and __import__("time").time() < deadline:
    app.processEvents()
    __import__("time").sleep(0.05)
app.processEvents()

print("---- result ----")
print("progress value :", win.progress.value())
print("stage label    :", win.lbl_stage.text())
print("step label     :", win.lbl_step.text())
print("msg label      :", win.lbl_msg.text())
print("step states    :", win._stage_states)
print("step texts     :", [l.text() for l in win.step_labels])
print("alerts         :", alerts)
print("btn re-enabled :", win.btn_start.isEnabled())
expected_failed = ["COLMAP 位姿求解", "3D 高斯泼溅训练", "泼溅网格重建",
                    "Blender 网格后处理", "多格式导出"]
got_failed = [STAGE_ORDER[i] for i, s in enumerate(win._stage_states)
              if s == "failed"]
print("failed match   :", got_failed == expected_failed, got_failed)
win.close()
print("VERIFY DONE")
