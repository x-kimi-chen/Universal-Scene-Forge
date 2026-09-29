# -*- coding: utf-8 -*-
"""0.9.6 前置模块单元测试: 主体遮罩跳过路径 / 训练器注册表 / RealityScan 配置。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ok = fail = 0


def check(tag, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"OK  {tag}")
    else:
        fail += 1
        print(f"FAIL {tag} {detail}")


from app.models.settings import PipelineSettings  # noqa: E402
from app.core.masking import SubjectMasker, rembg_available  # noqa: E402
from app.core import trainers  # noqa: E402
from app.dcc.realityscan_bridge import RealityScanBridge  # noqa: E402

s = PipelineSettings.load()

# 1) 遮罩模块: rembg 已装 → 真实图片生成遮罩 (白=主体)
import cv2
masker = SubjectMasker()
_timg = ROOT / "build/usf_selftest/mask_test_img.png"
cv2.imwrite(str(_timg), __import__("numpy").full((120, 160, 3), 200, dtype="uint8"))
r = masker.generate([_timg], ROOT / "build/usf_selftest/masks_t")
if rembg_available():
    check("mask.installed", r is not None and len(r) == 1)
else:
    check("mask.absent-graceful", r is None)
_timg.unlink(missing_ok=True)

# 2) 训练器注册表: 全量探测不抛异常; builtin 始终可回退
engines = trainers.available_engines(s)
check("trainer.registry", set(engines) == {"builtin", "opensplat",
                                          "nerfstudio", "postshot"})
check("trainer.builtin-detect", engines["builtin"].available)
# 所选引擎不可用时回退内置
s.trainer = "opensplat"          # 本机无 opensplat → 应回退
eng = trainers.create_engine(s)
check("trainer.fallback-builtin", isinstance(eng, trainers.Builtin3dgsAdapter))
s.trainer = "builtin"
eng2 = trainers.create_engine(s)
check("trainer.builtin-select", isinstance(eng2, trainers.Builtin3dgsAdapter))
# nerfstudio 骨架: 未装环境时明确报骨架状态
ns = trainers.NerfstudioAdapter.detect(s)
check("trainer.nerfstudio-installed", ns.available and "ns-train" in ns.detail,
      ns.detail)

# 3) RealityScan 桥接: 可实例化 + CLI 脚本按惯例生成 + 未装优雅由调用方处理
b = RealityScanBridge("RealityScan.exe", timeout_s=10)
check("rscan.bridge-ok", b.exe == "RealityScan.exe")
out = ROOT / "build/usf_selftest/rscan_t/out.obj"
try:
    b.reconstruct_and_export(Path("images"), out)
    check("rscan.run", False, "无 RealityScan 环境应失败")
except Exception as e:  # noqa: BLE001 —— 骨架预期: 无软件时命令失败
    check("rscan.graceful-fail", "RealityScan" in str(e) or out.parent.exists())

# 4) settings 新字段就位
check("settings.fields",
      s.trainer == "builtin"
      and hasattr(s, "realityscan_exe")
      and hasattr(s, "opensplat_exe")
      and hasattr(s, "postshot_cli"))

print(f"\n0.9.6-PREP: {ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
