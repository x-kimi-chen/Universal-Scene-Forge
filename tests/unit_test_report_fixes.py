# -*- coding: utf-8 -*-
"""单元测试: 第二轮测试报告(test002)缺陷修复锁定。

对应修复项:
- P1  COLMAP 检测不一致: SfmRunner 三级兜底(显式路径 → PATH → tools_root 便携版)
- P2  相机模型不匹配: feature_extractor 强制 --ImageReader.camera_model PINHOLE
- P3  project.json 状态语义: status 字段区分 success/failed/skipped
- P4  主动跳过语义: StageSkipped 不计入 failed_stages, 发 stage_skipped 信号
- P5  日志误红: console handler 绑定 stdout 而非 stderr
- P6  主窗口最小尺寸 980x640
- EXT DCC 扩展统一管理: 体检三态 + installable 过滤 + 一键安装容错
- V   手册/版本 0.9.4 同步 + DCC 扩展章节
"""
import json
import logging
import os
import shutil
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


_tmp = Path(tempfile.mkdtemp(prefix="usf_report_fix_"))

# --------------------------------------------------------------- #
# P1) COLMAP 三级兜底
# --------------------------------------------------------------- #
from app.core.colmap import SfmRunner, SfmError

_orig_which = shutil.which
try:
    # P1a: PATH 命中 → 使用 PATH 版
    shutil.which = lambda name: r"C:\PATH\colmap.bat" if name == "colmap" else None
    r1 = SfmRunner()
    check("P1a", "PATH 命中 colmap → 使用", r1.colmap == r"C:\PATH\colmap.bat", str(r1.colmap))

    # P1b: 显式路径优先于 PATH
    r2 = SfmRunner(colmap_exe="X:/explicit/colmap.exe")
    check("P1b", "显式 colmap_exe 优先于 PATH",
          r2.colmap == "X:/explicit/colmap.exe", str(r2.colmap))

    # P1c: PATH 未命中 → tools_root 便携版兜底
    shutil.which = lambda name: None
    _tools = _tmp / "tools"
    _portable = _tools / "colmap" / "COLMAP-3.9.1-windows-cuda" / "COLMAP.bat"
    _portable.parent.mkdir(parents=True)
    _portable.write_text("")
    r3 = SfmRunner(tools_root=str(_tools))
    check("P1c", "PATH 未命中 → tools_root 便携版兜底",
          Path(r3.colmap) == _portable, str(r3.colmap))

    # P1d: 三级全部落空 → build_dataset 明确报错
    r4 = SfmRunner()
    try:
        r4.build_dataset([], _tmp / "no_colmap_work")
        check("P1d", "三级落空 → SfmError 提示可一键安装", False)
    except SfmError as exc:
        check("P1d", "三级落空 → SfmError 提示可一键安装",
              "COLMAP 未安装" in str(exc))
finally:
    shutil.which = _orig_which

# --------------------------------------------------------------- #
# P2) PINHOLE 相机模型
# --------------------------------------------------------------- #
_captured = []
_runner = SfmRunner(colmap_exe="X:/fake/colmap.bat")
_runner._run = lambda cmd: _captured.append(list(cmd))
_imgs = _tmp / "src"
_imgs.mkdir(exist_ok=True)
for i in range(3):
    (_imgs / f"frame_{i}.jpg").write_bytes(b"\xff\xd8fake")
_work = _tmp / "sfm_work"
_sparse0 = _work / "dataset" / "sparse" / "0"   # 预置产物, 模拟 mapper 成功
_sparse0.mkdir(parents=True)
(_sparse0 / "cameras.bin").write_bytes(b"fake")
_dataset = _runner.build_dataset(list(_imgs.glob("*.jpg")), _work)

check("P2a", "build_dataset 返回数据集目录", _dataset == _work / "dataset")
check("P2b", "三步 SfM 全部执行(feature/matcher/mapper)",
      [c[1] for c in _captured] == ["feature_extractor", "exhaustive_matcher", "mapper"],
      str([c[1] for c in _captured]))
_fx = _captured[0]
try:
    _model = _fx[_fx.index("--ImageReader.camera_model") + 1]
except ValueError:
    _model = None
check("P2c", "特征提取强制 PINHOLE 相机模型", _model == "PINHOLE", str(_model))
check("P2d", "single_camera=1 保持（多帧同一相机）",
      _fx[_fx.index("--ImageReader.single_camera") + 1] == "1")
check("P2e", "抽帧图像平坦拷贝进 dataset/images",
      len(list((_work / "dataset" / "images").glob("*.jpg"))) == 3)

# --------------------------------------------------------------- #
# P3) project.json status 字段
# --------------------------------------------------------------- #
from app.models.project import StageRecord, ProjectState

check("P3a", "StageRecord.status: success",
      StageRecord("a", True).status == "success")
check("P3b", "StageRecord.status: failed",
      StageRecord("b", False).status == "failed")
check("P3c", "StageRecord.status: skipped(ok=None)",
      StageRecord("c", None, skipped=True).status == "skipped")

_state = ProjectState(source="s", work_dir=str(_tmp))
_state.record("成功段", True, 1.0)
_state.record("失败段", False, 2.0, message="boom")
_state.record("跳过段", None, 0.0, skipped=True, message="宿主未安装")
_pj = _tmp / "project.json"
_state.save(_pj)
_data = json.loads(_pj.read_text(encoding="utf-8"))
_statuses = [r["status"] for r in _data["records"]]
check("P3d", "project.json 序列化 status 序列",
      _statuses == ["success", "failed", "skipped"], str(_statuses))
check("P3e", "skipped 行 ok=null + message 留档",
      _data["records"][2]["ok"] is None and _data["records"][2]["skipped"] is True
      and _data["records"][2]["message"] == "宿主未安装")

# --------------------------------------------------------------- #
# P4) StageSkipped 语义（_safe 容错框架）
# --------------------------------------------------------------- #
from PySide6.QtWidgets import QApplication
from app.controllers.pipeline_controller import (
    PipelineController, EventBus, StageSkipped)
from app.models.settings import PipelineSettings

_app = QApplication.instance() or QApplication(sys.argv)
_bus = EventBus()
_ctrl = PipelineController(PipelineSettings(), _bus, dcc_infos={"__test__": None})
_skipped_sig, _failed_sig = [], []
_bus.stage_skipped.connect(lambda n: _skipped_sig.append(n))
_bus.stage_failed.connect(lambda n: _failed_sig.append(n))


def _raise_skip():
    raise StageSkipped("UE5 未安装, 实时预览跳过")


def _raise_fail():
    raise RuntimeError("boom")


_ctrl._safe("阶段A-跳过", _raise_skip)
_ctrl._safe("阶段B-失败", _raise_fail)
_ctrl._safe("阶段C-成功", lambda: None)

_rec = {r.name: r for r in _ctrl.state.records}
check("P4a", "StageSkipped → status=skipped 且不计入 failed_stages",
      _rec["阶段A-跳过"].status == "skipped"
      and "阶段A-跳过" not in _ctrl.failed_stages)
check("P4b", "真失败 → status=failed 且计入 failed_stages",
      _rec["阶段B-失败"].status == "failed"
      and _ctrl.failed_stages == ["阶段B-失败"])
check("P4c", "成功 → status=success", _rec["阶段C-成功"].status == "success")
check("P4d", "信号分流: stage_skipped 与 stage_failed 各自发射",
      _skipped_sig == ["阶段A-跳过"] and _failed_sig == ["阶段B-失败"])
check("P4e", "跳过与失败在留档中可区分(ok=None vs ok=False)",
      _rec["阶段A-跳过"].ok is None and _rec["阶段B-失败"].ok is False)

# --------------------------------------------------------------- #
# P5) 日志 handler 绑定 stdout
# --------------------------------------------------------------- #
from app.utils.logger import setup_logging

setup_logging()
_handlers = logging.getLogger().handlers
check("P5a", "root logger 含 stdout StreamHandler",
      any(isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
          for h in _handlers))
check("P5b", "无裸 stderr console handler（避免 PowerShell 渲染为错误红字）",
      not any(isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
              for h in _handlers))

# --------------------------------------------------------------- #
# P6/EXT) 主窗口 UI 源码级锁定
# --------------------------------------------------------------- #
_mw_src = (ROOT / "app" / "views" / "main_window.py").read_text(encoding="utf-8")
check("P6", "主窗口最小尺寸约束 980x640", "setMinimumSize(980, 640)" in _mw_src)
check("EXT0a", "体检表合并扩展行(check_extensions)",
      "extension_manager.check_extensions" in _mw_src)
check("EXT0b", "「安装全部 DCC 扩展」按钮存在", "安装全部 DCC 扩展" in _mw_src)
check("EXT0c", "表格行渲染区分 ext 类型(琥珀色提示)",
      'd.kind == "ext"' in _mw_src)

# --------------------------------------------------------------- #
# EXT) extension_manager 行为
# --------------------------------------------------------------- #
from app.dcc import extension_manager as em
from app.dcc import premiere_bridge as pb

_s = PipelineSettings()
_orig_detect, _orig_dir = pb.detect_premiere, pb.extension_install_dir
try:
    # E1: 宿主未安装 → 暂不需要
    pb.detect_premiere = lambda *a, **k: None
    _res = em.check_extensions(_s)
    check("E1", "宿主未安装 → ok=True 且提示暂不需要",
          len(_res) == 1 and _res[0].ok and "暂不需要" in _res[0].detail,
          str([r.detail for r in _res]))

    # E2: 宿主已装 + 扩展已装 → 就绪
    pb.detect_premiere = lambda *a, **k: r"C:\fake\Premiere.exe"
    _ext_dir = _tmp / "cep_ext"
    pb.extension_install_dir = lambda: _ext_dir
    (_ext_dir / "CSXS").mkdir(parents=True, exist_ok=True)
    (_ext_dir / "CSXS" / "manifest.xml").write_text("<xml/>")
    _res = em.check_extensions(_s)
    check("E2", "宿主+扩展均已装 → ok=True 已装入",
          _res[0].ok and "已装入" in _res[0].detail)

    # E3: 宿主已装 + 扩展未装 → 待安装, 提示一键安装
    (_ext_dir / "CSXS" / "manifest.xml").unlink()
    _res = em.check_extensions(_s)
    check("E3", "宿主已装而扩展未装 → ok=False 且 fix_hint 指向一键安装",
          not _res[0].ok and _res[0].detail == "未安装"
          and "安装全部 DCC 扩展" in (_res[0].fix_hint or ""))

    # E4: installable 过滤（宿主未装/已装扩展均排除）
    pb.detect_premiere = lambda *a, **k: None
    check("E4a", "宿主未安装 → installable 为空", em.installable(_s) == [])
    pb.detect_premiere = lambda *a, **k: r"C:\fake\Premiere.exe"
    check("E4b", "宿主已装未装扩展 → installable 含 pr_cep",
          [e.key for e in em.installable(_s)] == ["pr_cep"])
    (_ext_dir / "CSXS" / "manifest.xml").write_text("<xml/>")
    check("E4c", "扩展已就绪 → installable 为空（不重装）",
          em.installable(_s) == [])

    # E5: 一键安装容错——单个失败不阻断其余
    _orig_ext_list = em.EXTENSIONS

    def _boom():
        raise PermissionError("目标目录被占用")

    em.EXTENSIONS = [
        em.DccExtensionSpec(
            key="ok_ext", label="扩展OK", host_label="宿主X",
            host_present=lambda st: True, installed=lambda st: False,
            install=lambda: Path("C:/fake/ok")),
        em.DccExtensionSpec(
            key="bad_ext", label="扩展BAD", host_label="宿主Y",
            host_present=lambda st: True, installed=lambda st: False,
            install=_boom),
    ]
    try:
        _lines = []
        _results = em.install_all(_s, _lines.append)
        check("E5a", "安装结果逐项返回(ok/失败)",
              _results == [("扩展OK", True, str(Path("C:/fake/ok"))),
                           ("扩展BAD", False, "目标目录被占用")],
              str(_results))
        check("E5b", "控制台回显含 ✔ 与 ✘ 行",
              any(l.startswith("✔") for l in _lines)
              and any(l.startswith("✘") for l in _lines))
    finally:
        em.EXTENSIONS = _orig_ext_list
finally:
    pb.detect_premiere, pb.extension_install_dir = _orig_detect, _orig_dir

check("E6", "EXTENSIONS 注册表含 pr_cep (Premiere CEP 面板)",
      [e.key for e in em.EXTENSIONS] == ["pr_cep"])

# --------------------------------------------------------------- #
# V) 手册与版本 0.9.4 同步
# --------------------------------------------------------------- #
_manual = (ROOT / "docs" / "user_manual.html").read_text(encoding="utf-8")
check("V1", "手册版本标识 v0.9.4", "用户手册 v0.9.4" in _manual)
check("V2", "手册 0.9.3 仅存历史标注(v0.9.3 起)",
      _manual.count("0.9.3") == 1 and "v0.9.3 起" in _manual,
      f"count={_manual.count('0.9.3')}")
check("V3", "手册含「DCC 扩展一键安装」章节",
      "DCC 扩展一键安装" in _manual and "安装全部 DCC 扩展" in _manual)
check("V4", "手册体检表含 DCC 扩展行", "<td>DCC 扩展</td>" in _manual)

# --------------------------------------------------------------- #
print()
if failures:
    print(f"FAILED: {len(failures)} 项未通过")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("unit_test_report_fixes: 全部通过")
