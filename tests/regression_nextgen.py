# -*- coding: utf-8 -*-
"""回归测试: 新增 DCC 探测 / 设置字段 / 桥接装配 / 手册路径。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.models.settings import PipelineSettings
from app.dcc.registry_probe import DccDetector

failures = []

# 1) 设置字段与 DCC_PATH_FIELDS 一致性
from app.views.main_window import DCC_PATH_FIELDS
s = PipelineSettings()
for key, name, field in DCC_PATH_FIELDS:
    if not hasattr(s, field):
        failures.append(f"settings 缺字段: {field}")
print(f"[1] DCC_PATH_FIELDS 共 {len(DCC_PATH_FIELDS)} 项, 字段校验 {'OK' if not failures else failures}")

# 2) 全量探测 13 款软件(含 8 款次世代新增)
det = DccDetector({})
infos = det.detect_all()
expected = {k for k, _n, _f in DCC_PATH_FIELDS}
missing = expected - set(infos)
extra = set(infos) - expected
if missing:
    failures.append(f"探测器缺 key: {missing}")
if extra:
    failures.append(f"探测器多 key: {extra}")
avail = sorted(k for k, i in infos.items() if getattr(i, "available", False))
print(f"[2] 探测 {len(infos)} 款: 本机可用={avail if avail else '无(便携/沙箱环境属正常)'}")
for k in sorted(expected):
    i = infos.get(k)
    print(f"    {k:12s} available={getattr(i,'available',None)} exe={getattr(i,'exe',None)}")

# 3) 手动覆盖优先: 伪造一个 exe 指给 zbrush
fake = ROOT / "_test_fake_zbrush.exe"
fake.write_bytes(b"MZ")
det2 = DccDetector({"zbrush": str(fake)})
info2 = det2.detect_all().get("zbrush")
ok_override = bool(info2 and info2.available and str(info2.exe) == str(fake))
print(f"[3] 手动路径覆盖优先级: {'OK' if ok_override else 'FAIL'}")
if not ok_override:
    failures.append("zbrush 手动覆盖未生效")
fake.unlink(missing_ok=True)

# 4) 桥接类可实例化(超时看门狗参数)
from app.dcc.houdini_bridge import HoudiniBridge
from app.dcc.c4d_bridge import C4dBridge
import threading
ev = threading.Event()
hb = HoudiniBridge("C:/fake/hython.exe", 1800, stop_event=ev)
cb = C4dBridge("C:/fake/Commandline.exe", 1800, stop_event=ev)
print(f"[4] HoudiniBridge/C4dBridge 实例化: OK (timeout={hb.timeout_s}/{cb.timeout_s})")

# 5) ExportDispatcher 支持新桥接参数
from app.core.exporter import ExportDispatcher
d = ExportDispatcher(blender_bridge=None, ue5_bridge=None, max_bridge=None,
                     maya_bridge=None, houdini_bridge=hb, c4d_bridge=cb)
print(f"[5] ExportDispatcher 装配 houdini/c4d 桥: OK")

# 6) 文档随包路径解析
from app.utils.paths import docs_dir
manual = docs_dir() / "user_manual.html"
deploy = docs_dir() / "deployment_flow.html"
print(f"[6] 用户手册存在={manual.exists()}  部署流程图存在={deploy.exists()}")
if not manual.exists():
    failures.append("user_manual.html 缺失")
if not deploy.exists():
    failures.append("deployment_flow.html 缺失")

# 7) HTML 基础合法性(标签配对粗检)
for f in (manual, deploy):
    txt = f.read_text(encoding="utf-8")
    for tag in ("section", "figure", "table", "svg"):
        o, c = txt.count(f"<{tag}"), txt.count(f"</{tag}>")
        if o != c:
            failures.append(f"{f.name}: <{tag}> 标签不配对 {o}/{c}")
print("[7] HTML 标签配对粗检: " + ("OK" if not any('标签不配对' in x for x in failures) else "FAIL"))

print()
if failures:
    print("=== 回归失败 ===")
    for x in failures:
        print(" -", x)
    sys.exit(1)
print("=== 全部回归通过 ===")
