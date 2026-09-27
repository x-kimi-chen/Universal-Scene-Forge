# -*- coding: utf-8 -*-
"""单元测试: D-1 缺陷回归 — 依赖体检 open3d 误报修复。

背景: deps.py 的 REQUIRED_PY 曾以模块别名 "o3d" 作为 find_spec() 探测键,
而 open3d 的真实导入名是 "open3d", 导致体检恒报「未安装」(D-1)。
本用例锁死该修复: 探测键必须等于真实导入名, 且体检结果与导入状态一致。

模拟策略(两种缺失态互不等价, 分别覆盖):
- 体检路径  check_python() → importlib.util.find_spec(): 用 find_spec 包装补丁返回 None
- 导入路径  to_mesh_obj() → import open3d: 用 sys.meta_path 拦截器抛 ImportError
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util

from app.utils import deps
from app.core.mesh import SplatMeshExtractor, MeshError

failures = []


def check(no, desc, ok, detail=""):
    print(f"[{no}] {desc}: {'OK' if ok else 'FAIL'}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"[{no}] {desc}")


# --------------------------------------------------------------- #
# 1) D-1 核心断言: 探测键必须使用真实导入名, 禁止别名回归
# --------------------------------------------------------------- #
check("1a", "REQUIRED_PY 含真实导入名 open3d 键", "open3d" in deps.REQUIRED_PY)
check("1b", "REQUIRED_PY 不含别名 o3d 键(防 D-1 回归)", "o3d" not in deps.REQUIRED_PY)
EXPECTED_MAPPING = {
    "PySide6": "PySide6", "numpy": "numpy",
    "cv2": "opencv-python-headless", "plyfile": "plyfile",
    "requests": "requests", "open3d": "open3d",
}
check("1c", "REQUIRED_PY 键值映射与设计契约一致(键=导入名, 值=pip 包名)",
      dict(deps.REQUIRED_PY) == EXPECTED_MAPPING, str(deps.REQUIRED_PY))

# --------------------------------------------------------------- #
# 2) 体检结果与真实导入状态逐项一致(关键属性: 不误报)
# --------------------------------------------------------------- #
report = deps.check_python()
by_name = {d.name: d for d in report}
for mod, pkg in deps.REQUIRED_PY.items():
    real = importlib.util.find_spec(mod) is not None
    got = by_name.get(pkg)
    check(f"2-{mod}", f"体检项 {pkg} 与真实导入状态一致",
          got is not None and got.ok == real,
          f"real={real} reported={None if got is None else got.ok}")

# --------------------------------------------------------------- #
# 3) open3d 已安装场景: 体检必须报 ok(本 venv 已安装 open3d)
# --------------------------------------------------------------- #
o3d_item = by_name.get("open3d")
if importlib.util.find_spec("open3d") is not None:
    check("3", "open3d 已安装时体检报 ok=True 且不误报",
          o3d_item is not None and o3d_item.ok is True)
else:
    check("3", "open3d 未安装时体检报 ok=False(无 open3d 环境降级断言)",
          o3d_item is not None and o3d_item.ok is False)

# --------------------------------------------------------------- #
# 4) 体检路径缺失模拟: find_spec 对 open3d 返回 None(等价打包后状态)
# --------------------------------------------------------------- #
_orig_find_spec = importlib.util.find_spec


def _fake_find_spec(name, *args, **kwargs):
    if name == "open3d" or name.startswith("open3d."):
        return None  # 与「包内无 open3d」的真实 find_spec 行为一致
    return _orig_find_spec(name, *args, **kwargs)


importlib.util.find_spec = _fake_find_spec
try:
    report_blocked = deps.check_python()
    b3 = {d.name: d for d in report_blocked}.get("open3d")
    check("4a", "open3d 缺失时体检报 ok=False",
          b3 is not None and b3.ok is False)
    check("4b", "缺失项 fix_hint 给出 pip 安装指引",
          b3 is not None and "pip install open3d" in b3.fix_hint)
    check("4c", "其余五项不受拦截影响仍报 ok",
          all({d.name: d for d in report_blocked}[p].ok
              for p in EXPECTED_MAPPING.values() if p != "open3d"))
finally:
    importlib.util.find_spec = _orig_find_spec

# --------------------------------------------------------------- #
# 5) 导入路径缺失模拟: meta_path 拦截器阻断 import open3d
#    验证网格重建入口给出操作指引而非裸 ImportError
# --------------------------------------------------------------- #
class _BlockOpen3D:
    """sys.meta_path 钩子: 使 `import open3d` 抛 ImportError(模拟未安装)。"""
    def find_spec(self, name, path=None, target=None):
        if name == "open3d" or name.startswith("open3d."):
            raise ImportError(f"{name} blocked by test")


sys.meta_path.insert(0, _BlockOpen3D())
try:
    try:
        SplatMeshExtractor().to_mesh_obj(None, None, Path("unused.obj"))
        check("5", "to_mesh_obj 缺 open3d 时抛 MeshError", False, "未抛出异常")
    except MeshError as exc:
        msg = str(exc)
        check("5a", "to_mesh_obj 缺 open3d 时抛 MeshError", True)
        check("5b", "MeshError 消息含「依赖体检」指引", "依赖体检" in msg, msg)
        check("5c", "MeshError 消息含 pip install open3d 命令",
              "pip install open3d" in msg, msg)
    except Exception as exc:  # 裸异常 = 引导链路被破坏
        check("5", "to_mesh_obj 抛出的是 MeshError 而非裸异常",
              False, f"{type(exc).__name__}: {exc}")
finally:
    sys.meta_path.pop(0)
    importlib.invalidate_caches()

# --------------------------------------------------------------- #
# 6) 解除模拟后体检恢复真实状态(无残留副作用)
# --------------------------------------------------------------- #
report_restored = deps.check_python()
r3 = {d.name: d for d in report_restored}.get("open3d")
expect = importlib.util.find_spec("open3d") is not None
check("6", "解除拦截后体检结果恢复正常", r3 is not None and r3.ok is expect)

# --------------------------------------------------------------- #
# 7) 体检报告结构完整性: python + tool + cuda 三类齐全
# --------------------------------------------------------------- #
full = deps.full_report()
kinds = {d.kind for d in full}
check("7a", "full_report 含 python/tool/cuda 三类", {"python", "tool", "cuda"} <= kinds)
check("7b", "missing() 只挑出未通过项",
      all((not d.ok) == (d in deps.missing(full)) for d in full))

print()
if failures:
    print("=== 单元测试失败 ===")
    for x in failures:
        print(" -", x)
    sys.exit(1)
print("=== 全部单元测试通过 ===")
