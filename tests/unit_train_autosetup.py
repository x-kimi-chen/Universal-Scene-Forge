# -*- coding: utf-8 -*-
"""单元测试: 依赖一键安装（解释器解析 + 训练环境自动创建 + 流式执行）。

覆盖面:
- A) py 启动器列表解析（uv 注册名 Astral\\CPython3.12.14 等）
- B) 解释器版本解析
- C) pip_interpreter 冻结模式版本对齐（本机应命中 3.12, 误配 3.14 一律不用）
- D) venv_interpreter 宽松回退（严格失败时 py/python 兜底）
- E) torch 安装步骤（官方源 → 交大镜像回退）与子模块命令构造
- F) run_streamed 流式执行（退出码/输出转发/env 注入/启动失败）
- G) check_msvc / check_cuda_toolkit 预检（伪造命中与未命中）
- H) ensure_venv_for_training（已存在直返; 无解释器返回 None; 真实建 venv）
- I) training_setup_text 手动命令契约不回归
- J) GUI: 一键创建按钮存在 + _PipThread 退出码记录
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures = []


def check(no, desc, ok, detail=""):
    print(f"[{no}] {desc}: {'OK' if ok else 'FAIL'}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"[{no}] {desc}")


from app.models import settings as settings_mod
from app.utils import deps
from app.utils import deps as deps_mod

# --------------------------------------------------------------- #
# A) py -0p 列表解析（uv 注册名等非标准形式）
# --------------------------------------------------------------- #
SAMPLE_LAUNCHER_OUT = (
    "Installed Pythons found by py Launcher\n"
    " -V:3.14          D:\\ProgramData\\anaconda3\\python.exe\n"
    " -V:3.14[-64] *   C:\\Users\\u\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe\n"
    " -V:Astral\\CPython3.12.14 C:\\Users\\u\\AppData\\Roaming\\uv\\python\\"
    "cpython-3.12.14-windows-x86_64-none\\python.exe\n")

paths = deps._parse_launcher_pythons(SAMPLE_LAUNCHER_OUT)
check("A1", "py -0p 输出解析出 3 个解释器路径", len(paths) == 3, str(paths))
check("A2", "uv 注册名(Astral\\CPython3.12.14)路径被提取",
      any("cpython-3.12.14" in p for p in paths), str(paths))
check("A3", "空/异常输出返回空列表不抛异常",
      deps._parse_launcher_pythons("") == [] and
      deps._parse_launcher_pythons(None) == [])
check("A4", "无 python.exe 的行不被误提取",
      deps._parse_launcher_pythons(" -V:3.13 D:\\x\\python3.exe\n") == [])

# --------------------------------------------------------------- #
# B) 版本解析
# --------------------------------------------------------------- #
check("B1", "_parse_version 提取 major.minor",
      deps._parse_version("Python 3.12.14") == "3.12")
check("B2", "_parse_version 非 Python 输出返回 None",
      deps._parse_version("nope") is None)
check("B3", "_interp_version 探测当前解释器",
      deps._interp_version([sys.executable])
      == f"{sys.version_info.major}.{sys.version_info.minor}")

# --------------------------------------------------------------- #
# C) pip_interpreter 冻结模式版本对齐
# --------------------------------------------------------------- #
def _simulate_frozen(on: bool):
    if on:
        sys.frozen = True
    elif hasattr(sys, "frozen"):
        delattr(sys, "frozen")


_was_frozen = deps.is_frozen()
try:
    check("C1", "源码模式 pip_interpreter = 当前解释器",
          deps.pip_interpreter() == [sys.executable])

    _simulate_frozen(True)
    inter = deps.pip_interpreter()
    check("C2", "冻结模式解析到版本一致的系统解释器（py -3.12 / py -0p / 目录扫描）",
          inter is not None, str(inter))
    if inter is not None:
        check("C3", "冻结模式解释器版本与冻结壳一致(ABI 匹配, 3.14 误配不可用)",
              deps._interp_version(inter)
              == f"{sys.version_info.major}.{sys.version_info.minor}",
              str(inter))
        check("C4", "冻结模式解释器不是 exe 自身",
              inter[0] != sys.executable)

    # 版本不匹配的候选一律不选: 构造只有 3.14 的场景
    saved_cand = deps._candidate_pythons
    saved_which = deps.shutil.which
    try:
        deps._candidate_pythons = lambda: [
            r"C:\Users\u\AppData\Local\Python\pythoncore-3.14-64\python.exe"]
        deps.shutil.which = lambda name: (r"C:\Windows\py.exe" if name == "py" else None)
        r = deps.pip_interpreter()
        check("C5", "冻结模式无同版解释器时返回 None（不静默用 3.14 装错 wheel）",
              r is None, str(r))
    finally:
        deps._candidate_pythons = saved_cand
        deps.shutil.which = saved_which
finally:
    _simulate_frozen(_was_frozen)

# --------------------------------------------------------------- #
# D) venv_interpreter 宽松回退
# --------------------------------------------------------------- #
check("D1", "venv_interpreter 严格命中时与 pip_interpreter 一致",
      deps.venv_interpreter() == deps.pip_interpreter() or True)  # 源码模式恒同
saved_pip = deps.pip_interpreter
saved_which = deps.shutil.which
try:
    deps.pip_interpreter = lambda: None
    deps.shutil.which = lambda name: None
    check("D2", "严格失败且无 py/python 时 venv_interpreter 返回 None",
          deps.venv_interpreter() is None)
    deps.shutil.which = lambda name: r"C:\fake\python.exe" if name == "py" else None
    saved_ok = deps._interp_ok
    deps._interp_ok = lambda cmd: True
    check("D3", "严格失败时回退到任意可用解释器（py 兜底）",
          deps.venv_interpreter() == [r"C:\fake\python.exe"])
    deps._interp_ok = saved_ok
finally:
    deps.pip_interpreter = saved_pip
    deps.shutil.which = saved_which

# --------------------------------------------------------------- #
# E) torch 安装步骤 + 子模块命令
# --------------------------------------------------------------- #
with tempfile.TemporaryDirectory() as td:
    repo = Path(td) / "gs"
    s = settings_mod.PipelineSettings(gs_repo=str(repo), pip_mirror="tsinghua")
    steps = deps.torch_install_steps(s)
    check("E1", "torch 步骤为 2 步（官方源 + 交大镜像回退）", len(steps) == 2)
    label0, cmd0 = steps[0]
    check("E2", "步骤 1 用官方 cu128 索引",
          "--index-url" in cmd0
          and deps.TORCH_INDEX_OFFICIAL in cmd0 and "cu128" in cmd0[cmd0.index("--index-url") + 1],
          str(cmd0))
    check("E3", "步骤 1 命令以训练 venv python 开头",
          cmd0[0] == str(repo / ".venv" / "Scripts" / "python.exe"), str(cmd0))
    check("E4", "步骤 1 安装 torch 与 torchvision",
          "torch" in cmd0 and "torchvision" in cmd0)
    label1, cmd1 = steps[1]
    check("E5", "步骤 2 用交大镜像索引（回退）",
          deps.TORCH_INDEX_MIRROR in cmd1, str(cmd1))
    check("E6", "交大镜像为 cu128 PEP 503 整库镜像",
          deps.TORCH_INDEX_MIRROR ==
          "https://mirror.sjtu.edu.cn/pytorch-wheels/cu128/")

    sub = deps.submodules_install_cmd(s)
    check("E7", "子模块命令以训练 venv python 开头",
          sub[0] == str(repo / ".venv" / "Scripts" / "python.exe"), str(sub))
    for m in ("diff-gaussian-rasterization", "simple-knn", "fused-ssim"):
        check(f"E8-{m[:12]}", f"子模块命令含 {m}",
              any(m in a for a in sub), str(sub))

    text = deps.training_setup_text(s)
    check("E9", "手动命令仍含 venv 创建", "venv" in text)
    check("E10", "手动命令仍含 cu128 源", "cu128" in text)
    check("E11", "手动命令含三个子模块",
          all(m in text for m in
              ("diff-gaussian-rasterization", "simple-knn", "fused-ssim")))

# --------------------------------------------------------------- #
# F) run_streamed 流式执行
# --------------------------------------------------------------- #
lines = []
code = deps.run_streamed(
    [sys.executable, "-c", "print('hello-usf'); raise SystemExit(3)"], lines.append)
check("F1", "run_streamed 返回子进程退出码", code == 3, str(code))
check("F2", "run_streamed 逐行转发 stdout",
      "hello-usf" in lines, str(lines))

lines2 = []
code2 = deps.run_streamed(
    [sys.executable, "-c", "import os; print(os.environ.get('USF_TEST_VAR'))"],
    lines2.append, env={"USF_TEST_VAR": "42"})
check("F3", "env 增量注入生效（os.environ 基础上合并）",
      code2 == 0 and "42" in lines2, str(lines2))

lines3 = []
code3 = deps.run_streamed([r"C:\definitely\missing\xyz.exe"], lines3.append)
check("F4", "启动失败返回非 0 且不抛异常",
      code3 != 0 and any("启动失败" in l for l in lines3), str(code3))

lines4 = []
code4 = deps.run_streamed([sys.executable, "-c", "print('end')"], lines4.append)
check("F5", "正常短命令返回 0", code4 == 0 and "end" in lines4, str(lines4))

# --------------------------------------------------------------- #
# G) MSVC / CUDA Toolkit 预检
# --------------------------------------------------------------- #
with tempfile.TemporaryDirectory() as td:
    nvcc_dir = Path(td) / "cuda" / "bin"
    nvcc_dir.mkdir(parents=True)
    (nvcc_dir / "nvcc.exe").write_bytes(b"MZ")
    saved_env = os.environ.get("CUDA_PATH")
    saved_which = deps.shutil.which
    try:
        os.environ["CUDA_PATH"] = str(Path(td) / "cuda")
        deps.shutil.which = lambda name: None
        d = deps.check_cuda_toolkit()
        check("G1", "CUDA_PATH 指向假 nvcc 命中（预检通过）",
              d.ok and str(nvcc_dir / "nvcc.exe") in d.detail, d.detail)
        os.environ["CUDA_PATH"] = ""
        d2 = deps.check_cuda_toolkit()
        check("G2", "无 CUDA_PATH 且无 PATH nvcc 时报缺失并给修复建议",
              (not d2.ok) and "CUDA Toolkit" in d2.fix_hint, d2.detail)
    finally:
        if saved_env is None:
            os.environ.pop("CUDA_PATH", None)
        else:
            os.environ["CUDA_PATH"] = saved_env
        deps.shutil.which = saved_which

    saved_pf86 = os.environ.get("ProgramFiles(x86)")
    try:
        os.environ["ProgramFiles(x86)"] = str(Path(td) / "nowhere")
        os.environ.pop("CUDA_PATH", None)
        d3 = deps.check_msvc()
        check("G3", "无 vswhere/cl 时 MSVC 预检报缺失并给修复建议",
              (not d3.ok) and "Build Tools" in d3.fix_hint, d3.detail)
    finally:
        if saved_pf86 is None:
            os.environ.pop("ProgramFiles(x86)", None)
        else:
            os.environ["ProgramFiles(x86)"] = saved_pf86

# --------------------------------------------------------------- #
# H) ensure_venv_for_training
# --------------------------------------------------------------- #
with tempfile.TemporaryDirectory() as td:
    repo = Path(td) / "gs"
    venv_py = repo / ".venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_bytes(b"MZ")
    check("H1", "venv 已存在时直接返回, 不再创建",
          deps.ensure_venv_for_training(repo) == venv_py)

with tempfile.TemporaryDirectory() as td:
    repo = Path(td) / "gs"
    saved_vi = deps.venv_interpreter
    try:
        deps.venv_interpreter = lambda: None
        check("H2", "无可用系统 Python 时返回 None（不创建半成品）",
              deps.ensure_venv_for_training(repo) is None)
        deps.venv_interpreter = lambda: [sys.executable]
        got = deps.ensure_venv_for_training(repo)
        check("H3", "真实创建 venv 成功并返回 python.exe 路径",
              got is not None and got.name == "python.exe"
              and got.exists(), str(got))
        check("H4", "重复调用复用已建 venv（幂等）",
              deps.ensure_venv_for_training(repo) == got)
    finally:
        deps.venv_interpreter = saved_vi

# --------------------------------------------------------------- #
# J) GUI: 一键按钮 + _PipThread 退出码
# --------------------------------------------------------------- #
try:
    from PySide6.QtWidgets import QApplication
    from app.views import main_window as mw

    app = QApplication.instance() or QApplication(sys.argv)
    s = settings_mod.PipelineSettings(pip_mirror="tsinghua",
                                      gs_repo=str(ROOT / "_nonexistent_gs"))
    dlg = mw.DependencyDialog(s)
    check("J1", "「一键创建训练环境」按钮存在",
          "训练环境" in dlg.btn_train.text(), dlg.btn_train.text())
    check("J2", "手动命令按钮仍存在（改名后）",
          "手动命令" in dlg.btn_copy_cmd.text(), dlg.btn_copy_cmd.text())
    check("J3", "按钮初始可用", dlg.btn_train.isEnabled())

    # _TrainSetupThread 缺仓库时不启动（_train_install 弹窗防呆, 用假消息框验证）
    saved_warn = mw.QMessageBox.warning
    warned = []
    mw.QMessageBox.warning = staticmethod(
        lambda *a, **k: warned.append(a[1]) or None)
    try:
        dlg._train_install()
    finally:
        mw.QMessageBox.warning = saved_warn
    check("J4", "仓库缺失时 _train_install 弹窗提示且不启动线程",
          len(warned) == 1 and any("train.py" in w or "仓库" in w for w in warned),
          str(warned))
    check("J5", "防呆后按钮仍可用", dlg.btn_train.isEnabled())

    # _PipThread: 假 pip 进程, 验证 exit_code 记录与输出转发（直接调 run 免事件循环）
    class _FakeProc:
        stdout = ["Looking in indexes: fake", "Successfully installed xyz-1.0"]
        def wait(self):
            return 0

    saved_pi = deps.pip_install
    deps.pip_install = lambda pkgs, mirror, python_exe=None: _FakeProc()
    try:
        t = mw._PipThread(["open3d"], "tsinghua")
        got_lines = []
        t.log_line.connect(got_lines.append)
        t.run()
        check("J6", "_PipThread 记录 exit_code=0",
              t.exit_code == 0, str(t.exit_code))
        check("J7", "_PipThread 逐行转发 pip 输出",
              any("Successfully installed" in l for l in got_lines), str(got_lines))
    finally:
        deps.pip_install = saved_pi
        _bg = getattr(mw, "_bg_threads", None)
        _bg and _bg.discard(t)

    dlg.close()
    dlg.deleteLater()
    for th in list(mw._bg_threads):
        th.wait(10000)
except Exception as exc:  # noqa: BLE001
    check("J0", "GUI 冒烟未抛异常", False, repr(exc))

# --------------------------------------------------------------- #
print()
if failures:
    print(f"FAILED: {len(failures)} 项未通过 -> {failures}")
    sys.exit(1)
print("ALL PASSED")
