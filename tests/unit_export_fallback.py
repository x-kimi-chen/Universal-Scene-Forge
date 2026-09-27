# -*- coding: utf-8 -*-
"""验证导出回退链: MESH 缺失、仅 REPAIR(摄影测量修复网格)存在时,
_export 必须产出 fbx/glb/blend/obj。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from app.controllers.pipeline_controller import EventBus, PipelineController, Stage  # noqa: E402
from app.models.settings import PipelineSettings  # noqa: E402

app = QCoreApplication([])
bus = EventBus()
bus.log_line.connect(lambda lvl, msg: print(f"[{lvl}] {msg}"))

settings = PipelineSettings.load()
controller = PipelineController(settings, bus)
repaired = ROOT / "build/usf_selftest/msf_test/usf_scene_repaired.obj"
assert repaired.exists(), f"测试网格缺失: {repaired}"

# 模拟用户实战场景: MESH 阶段无产物, 仅 REPAIR 有修复网格
controller.state.artifacts[Stage.MESH.value] = []
controller.state.artifacts[Stage.REPAIR.value] = [str(repaired)]

out = ROOT / "build/usf_selftest/export_fallback_test"
if out.exists():
    import shutil
    shutil.rmtree(out)
# 导出位置重定向: 用户设置了导出地址 → 成品必须落在该目录
settings.export_dir = str(out)
results = controller._export(out)

print("---- result ----")
flat = []
for fmt, files in results.items():
    print(f"{fmt}: {[Path(f).name for f in files]}")
    flat.extend(files)
flat = [f for f in flat if f.exists() and f.stat().st_size > 0]
expect = {"fbx", "obj", "glb", "blend"}
got = {r for r, fs in results.items() if fs}
print("formats got:", sorted(got))
print("files non-empty:", len(flat))
assert expect <= got, f"缺少格式: {expect - got}"
assert (out / "usf_scene.mtl").exists(), "材质 mtl 未随行复制"
print("mtl sidecar: OK")
print("FALLBACK EXPORT VERIFY DONE")
