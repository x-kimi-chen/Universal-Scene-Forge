# -*- coding: utf-8 -*-
"""单元测试: QA 测试报告(USF_QA_Report.html) P0/P1/P2 缺陷修复锁定。

对应缺陷编号:
- B-01  3DGS 训练在冻结 EXE 中调用方式错误(解释器解析守卫 + 错误带输出尾部)
- B-02  --check-deps 在中文 Windows GBK 代码页下 UnicodeEncodeError 崩溃
- B-03  deps.check_cuda() 仅查 nvidia-smi 存在性, NVML 不可用时误报
- B-04  CLI/GUI DCC 探测结果不一致(设置覆盖未传给 CLI + 仅扫 C 盘)
- C-04  CLI 退出码语义化(0/1/2/3) + INGEST 失败提前终止
- C-06  日志 Unicode 标记(✔/✘)在 GBK 采集下显示为 \u2718 转义
- D-02  主 exe 无 Windows 资源版本(版本号 0.9.2 + version_info.txt)
"""
import io
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures = []


def check(no, desc, ok, detail=""):
    print(f"[{no}] {desc}: {'OK' if ok else 'FAIL'}"
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"[{no}] {desc}")


def _simulate_frozen(on: bool):
    if on:
        sys.frozen = True          # PyInstaller 在冻结壳内设置该属性
    elif hasattr(sys, "frozen"):
        delattr(sys, "frozen")


# --------------------------------------------------------------- #
# B-01) GaussianEngine 训练解释器解析
# --------------------------------------------------------------- #
from app.core.gaussian import GaussianEngine, GaussianTrainError

_tmp = Path(tempfile.mkdtemp(prefix="usf_qa_"))
_repo = _tmp / "gs_repo"
_repo.mkdir()
_was_frozen = hasattr(sys, "frozen")

try:
    _simulate_frozen(False)
    eng = GaussianEngine(gs_repo=_repo)
    check("B01a", "源码模式无显式解释器 → 当前解释器(合法回退)",
          Path(eng.python) == Path(sys.executable), eng.python)

    eng2 = GaussianEngine(gs_repo=_repo, python_exe=sys.executable)
    check("B01b", "显式 gs_python 存在 → 原样使用",
          Path(eng2.python) == Path(sys.executable), eng2.python)

    try:
        GaussianEngine(gs_repo=_repo, python_exe="X:/not_exist/python.exe")
        check("B01c", "显式 gs_python 不存在 → 报错(不再静默用坏路径)", False)
    except GaussianTrainError as exc:
        check("B01c", "显式 gs_python 不存在 → 报错(不再静默用坏路径)",
              "不存在" in str(exc))

    _venv_py = _repo / ".venv" / "Scripts" / "python.exe"
    _venv_py.parent.mkdir(parents=True, exist_ok=True)
    _venv_py.write_bytes(b"")
    eng3 = GaussianEngine(gs_repo=_repo)
    check("B01d", "无显式解释器但仓库含 .venv → 自动选用",
          Path(eng3.python) == _venv_py, str(eng3.python))

    _simulate_frozen(True)
    try:
        GaussianEngine(gs_repo=_tmp / "no_venv_repo")
        check("B01e", "冻结模式无 venv → 报错并附创建命令(不再拿 EXE 当解释器)",
              False)
    except GaussianTrainError as exc:
        msg = str(exc)
        check("B01e", "冻结模式无 venv → 报错并附创建命令(不再拿 EXE 当解释器)",
              "训练解释器不可用" in msg and "-m venv" in msg, msg[:120])
finally:
    _simulate_frozen(_was_frozen)

# B-01f) 训练失败时错误信息带输出尾部(C-05: 诊断文字与根因对齐)
_fail_repo = _tmp / "fail_repo"
_fail_repo.mkdir()
(_fail_repo / "train.py").write_text(
    "import sys\nprint('BOOM_MARKER_7788 dependency missing')\nsys.exit(3)\n",
    encoding="utf-8")
try:
    GaussianEngine(gs_repo=_fail_repo,
                   python_exe=sys.executable).train(
        _tmp / "dataset", _tmp / "model", iterations=10)
    check("B01f", "训练失败 → 异常含退出码与输出尾部", False)
except GaussianTrainError as exc:
    msg = str(exc)
    check("B01f", "训练失败 → 异常含退出码与输出尾部",
          "退出码 3" in msg and "BOOM_MARKER_7788" in msg, msg[:150])

# --------------------------------------------------------------- #
# B-02) --check-deps 在 GBK 代码页下不崩溃 + ASCII 标记
# --------------------------------------------------------------- #
import main as main_mod

_buf = io.BytesIO()
_gbk_out = io.TextIOWrapper(_buf, encoding="gbk", errors="strict")  # 模拟默认 GBK 控制台
_old_out, _old_err = sys.stdout, sys.stderr
sys.stdout = _gbk_out
_b02_ok = True
try:
    main_mod._reconfigure_stdio()          # 生产路径: main() 先重配再打印
    rc = main_mod._run_check_deps()
    _b02_ok = isinstance(rc, int)
except UnicodeEncodeError:
    _b02_ok = False
finally:
    try:
        _gbk_out.flush()   # TextIOWrapper 有内部写缓冲, 不 flush 则 BytesIO 读不到
    except (OSError, ValueError):
        pass
    sys.stdout, sys.stderr = _old_out, _old_err
_text = _buf.getvalue().decode("gbk", errors="replace")
check("B02a", "--check-deps 在 GBK 流上不抛 UnicodeEncodeError", _b02_ok)
check("B02b", "报告使用 ASCII 标记 [OK]/[X]",
      ("[OK]" in _text or "[X]" in _text)
      and "\u2714" not in _text and "\u2718" not in _text)

# --------------------------------------------------------------- #
# B-03) check_cuda 与 gpu.probe() 同源验证
# --------------------------------------------------------------- #
from app.utils import deps as deps_mod
from app.utils import gpu as gpu_mod

_orig_probe, _orig_which = gpu_mod.probe, deps_mod.shutil.which
try:
    gpu_mod.probe = lambda: gpu_mod.GpuInfo(
        available=True, name="RTX 5060 Laptop", driver="610.88", vram_mb=8192)
    _st = deps_mod.check_cuda()
    check("B03a", "probe 可用 → CUDA 驱动 OK 且带卡名/显存/驱动号",
          _st[0].ok and "RTX 5060" in _st[0].detail and "610.88" in _st[0].detail,
          _st[0].detail)

    gpu_mod.probe = lambda: gpu_mod.GpuInfo(available=False)
    deps_mod.shutil.which = lambda _n: "C:/fake/nvidia-smi.exe"
    _st2 = deps_mod.check_cuda()
    check("B03b", "smi 存在但 probe 失败 → 误报修复(报不可用并说明原因)",
          (not _st2[0].ok) and "查询失败" in _st2[0].detail, _st2[0].detail)

    gpu_mod.probe = lambda: gpu_mod.GpuInfo(available=False)
    deps_mod.shutil.which = lambda _n: None
    _st3 = deps_mod.check_cuda()
    check("B03c", "无 NVIDIA 驱动 → 报不可用(无 smi 措辞)",
          (not _st3[0].ok) and "未检测到 NVIDIA 驱动" in _st3[0].detail)
finally:
    gpu_mod.probe = _orig_probe
    deps_mod.shutil.which = _orig_which

# --------------------------------------------------------------- #
# B-04) CLI/GUI DCC 探测一致性
# --------------------------------------------------------------- #
from app.dcc import registry_probe as rp_mod

_dirs = rp_mod._program_files_dirs()
check("B04a", "安装根目录含环境变量 ProgramFiles",
      os.environ.get("ProgramFiles", "") in _dirs, str(_dirs))
check("B04b", "安装根目录全部真实存在(不残留不存在路径)",
      all(Path(d).is_dir() for d in _dirs), str(_dirs))
_d_pf = Path("D:/Program Files")
if _d_pf.is_dir():
    check("B04c", "D 盘 Program Files 也纳入扫描(QA 机器 Metashape 命中场景)",
          any(d.startswith("D:") for d in _dirs), str(_dirs))
else:
    print("[B04c] 本机无 D:\\Program Files, 跳过(仅断言多盘机制存在)")

from app.views import main_window as mw_mod
check("B04d", "DCC_PATH_FIELDS 单一数据源(main_window 转发自 registry_probe)",
      mw_mod.DCC_PATH_FIELDS is rp_mod.DCC_PATH_FIELDS)

from PySide6.QtCore import QCoreApplication
if QCoreApplication.instance() is None:
    QCoreApplication([])
from app.controllers.pipeline_controller import EventBus, PipelineController
from app.models.settings import PipelineSettings

_fake_exe = _tmp / "fake_blender.exe"
_fake_exe.write_bytes(b"")
_ctrl = PipelineController(PipelineSettings(blender_exe=str(_fake_exe)),
                           EventBus())
_bl = _ctrl.dcc_infos.get("blender")
check("B04e", "CLI(PipelineController) 同样吃 settings 路径覆盖",
      _bl is not None and _bl.available and _bl.source == "settings",
      f"{_bl and _bl.source} {_bl and _bl.exe}")

# --------------------------------------------------------------- #
# C-04) CLI 退出码语义 + INGEST 失败提前终止
# --------------------------------------------------------------- #
_rc = main_mod._run_cli([Path("Z:/definitely_not_exist_7788.mp4")],
                        _tmp / "cli_out")
check("C04a", "输入源不存在 → 退出码 3(环境阻断, 不进流水线)", _rc == 3, str(_rc))

_ctrl2 = PipelineController(PipelineSettings(), EventBus())
_ok_exec = _ctrl2.execute([_tmp / "no_such_video.mp4"], _tmp / "c04_out")
_recs = {r.name: r for r in _ctrl2.state.records}
from app.models.project import Stage
_sfm_rec = _recs.get(Stage.SFM.value)
check("C04b", "INGEST 失败 → 整体返回失败", not _ok_exec, str(_ok_exec))
check("C04c", "INGEST 失败 → 后续阶段留档为「未执行」而非连锁失败噪音",
      _sfm_rec is not None and _sfm_rec.skipped
      and "未执行" in _sfm_rec.message and not _sfm_rec.ok,
      str(_sfm_rec))
check("C04d", "8 个阶段全部留档(1 失败 + 7 跳过)", len(_ctrl2.state.records) == 8,
      str(len(_ctrl2.state.records)))

_readme = (ROOT / "README.md").read_text(encoding="utf-8")
check("C04e", "README 含退出码语义表(0/1/2/3)",
      "退出码" in _readme and "3" in _readme)

# --------------------------------------------------------------- #
# C-06) 探测日志 ASCII 标记
# --------------------------------------------------------------- #
_records = []


class _Cap(logging.Handler):
    def emit(self, record):
        _records.append(record.getMessage())


_cap = _Cap()
_probe_log = logging.getLogger("USF.PROBE")
_probe_log.setLevel(logging.INFO)   # root 默认 WARNING, 不设则 log.info 全被过滤
_probe_log.addHandler(_cap)
try:
    rp_mod.DccDetector({"zbrush": str(_fake_exe)}).detect_all()
finally:
    _probe_log.removeHandler(_cap)
_logs = "\n".join(_records)
check("C06", "探测日志无 Unicode 标记(✔/✘), GBK 采集不再出现 \\u2718 转义",
      "\u2714" not in _logs and "\u2718" not in _logs and "[OK]" in _logs,
      _logs[:150])

# --------------------------------------------------------------- #
# D-02) Windows 资源版本 + 版本号 0.9.4
# --------------------------------------------------------------- #
import app as app_mod

check("D02a", "版本号升级 0.9.4", app_mod.__version__ == "0.9.4",
      app_mod.__version__)
_vfile = ROOT / "installer" / "version_info.txt"
check("D02b", "PyInstaller version 资源文件存在且含 0.9.4",
      _vfile.exists() and "0.9.4" in _vfile.read_text(encoding="utf-8"))
_spec = (ROOT / "installer" / "UniversalSceneForge.spec").read_text(
    encoding="utf-8")
check("D02c", "spec 挂接 version 资源(--version-file)", "version_info.txt" in _spec)
_nsi = (ROOT / "installer" / "usf_setup.nsi").read_text(encoding="utf-8-sig")
check("D02d", "NSIS 版本同步 0.9.4", '"0.9.4"' in _nsi and "0.9.4.0" in _nsi)
check("D02e", "NSIS 写入卸载注册表 InstallLocation(C-01)", "InstallLocation" in _nsi)
check("D02f", "NSIS 快捷方式走所有用户上下文(C-07)",
      _nsi.count("SetShellVarContext all") >= 2)

# --------------------------------------------------------------- #
# deps 小修) torch 默认 cu128 + 训练环境命令一致性
# --------------------------------------------------------------- #
check("X01", "torch_cuda_command 默认 cu128(Blackwell 支持)",
      "cu128" in deps_mod.torch_cuda_command())
_setup_txt = deps_mod.training_setup_text(PipelineSettings())
check("X02", "训练环境命令块同样使用 cu128 源", "cu128" in _setup_txt)

# --------------------------------------------------------------- #
print()
if failures:
    print(f"*** {len(failures)} 项失败 ***")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("=== unit_qa_fixes 全部通过 ===")
