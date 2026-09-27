# -*- coding: utf-8 -*-
"""单元测试: 依赖体检多下载源 + 训练环境体检 + 工具便携版探测。

覆盖面:
- A) PIP_MIRRORS/PIP_MIRROR_LABELS 契约（键集一致、6 源、URL 合法）
- B) OFFICIAL_SOURCES 多源回退契约（Blender 3 源 / FFmpeg 2 源, urls 为列表）
- C) find_portable_exe 便携版探测（目录结构命中/未命中/不存在的 key）
- D) check_tools 三级查找（PATH→设置覆盖→tools_root 便携版）
- E) check_training_env / training_setup_text（仓库缺失/齐全两态 + 命令块构成）
- F) AutoInstaller._install_portable 多源回退（首源失败自动切换次源; 全源失败返回 None）
- G) DependencyDialog GUI 冒烟（offscreen）: 镜像下拉动态生成 + 切换即保存
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 无头 GUI 冒烟

failures = []


def check(no, desc, ok, detail=""):
    print(f"[{no}] {desc}: {'OK' if ok else 'FAIL'}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"[{no}] {desc}")


# --------------------------------------------------------------- #
# A) PIP 镜像源契约
# --------------------------------------------------------------- #
from app.models import settings as settings_mod

check("A1", "PIP_MIRRORS 含 6 个下载源", len(settings_mod.PIP_MIRRORS) == 6,
      str(len(settings_mod.PIP_MIRRORS)))
check("A2", "镜像键集与显示名键集一致(动态下拉数据源契约)",
      set(settings_mod.PIP_MIRRORS) == set(settings_mod.PIP_MIRROR_LABELS))
expected_keys = {"tsinghua", "aliyun", "tencent", "ustc", "huawei", "none"}
check("A3", "6 源 = 清华/阿里/腾讯/中科大/华为/官方",
      set(settings_mod.PIP_MIRRORS) == expected_keys,
      str(set(settings_mod.PIP_MIRRORS)))
bad_urls = [k for k, v in settings_mod.PIP_MIRRORS.items()
            if k != "none" and not v.startswith("https://")]
check("A4", "非官方源 URL 均为 https", not bad_urls, str(bad_urls))
check("A5", "none 源 = 空串(官方直连, pip 不加 -i)",
      settings_mod.PIP_MIRRORS["none"] == "")
check("A6", "settings.mirror_url 按键取值",
      settings_mod.PipelineSettings(pip_mirror="aliyun").mirror_url
      == settings_mod.PIP_MIRRORS["aliyun"])
check("A7", "mirror_url 未知键回退空串",
      settings_mod.PipelineSettings(pip_mirror="nope").mirror_url == "")

# --------------------------------------------------------------- #
# B) 工具下载源多源回退契约
# --------------------------------------------------------------- #
import requests

from app.dcc import auto_installer as ai

src = ai.OFFICIAL_SOURCES
check("B1", "Blender 3 个下载源(官方+NLUUG+Clarkson)",
      isinstance(src["blender"]["urls"], list) and len(src["blender"]["urls"]) == 3,
      str(src["blender"].get("urls")))
check("B2", "FFmpeg 4 个下载源(gyan.dev+GitHub+双国内镜像)",
      isinstance(src["ffmpeg"]["urls"], list) and len(src["ffmpeg"]["urls"]) == 4
      and any("gh-proxy.com" in u for u in src["ffmpeg"]["urls"])
      and any("ghfast.top" in u for u in src["ffmpeg"]["urls"]))
check("B3", "COLMAP 至少 1 个下载源",
      isinstance(src["colmap"]["urls"], list) and len(src["colmap"]["urls"]) >= 1)
all_https = [u for spec in src.values() for u in spec["urls"]
             if not u.startswith("https://")]
check("B4", "全部工具下载源均为 https", not all_https, str(all_https))
check("B5", "每源均有 exe_globs 定位解压后可执行文件",
      all(spec.get("exe_globs") for spec in src.values()))
check("B6", "REQUIRED_TOOLS 与 OFFICIAL_SOURCES 键对齐(ffmpeg/colmap 可一键装)",
      set(["ffmpeg", "colmap"]).issubset(src))

# --------------------------------------------------------------- #
# C) find_portable_exe 便携版探测
# --------------------------------------------------------------- #
with tempfile.TemporaryDirectory() as td:
    tools = Path(td)
    # 模拟 Blender 官方 zip 解压结构: blender/blender-4.1.1/blender.exe
    (tools / "blender" / "blender-4.1.1").mkdir(parents=True)
    (tools / "blender" / "blender-4.1.1" / "blender.exe").write_bytes(b"MZ")
    check("C1", "命中 blender-*/blender.exe glob",
          ai.find_portable_exe("blender", tools) is not None
          and ai.find_portable_exe("blender", tools).name == "blender.exe")
    check("C2", "未安装的工具返回 None",
          ai.find_portable_exe("ffmpeg", tools) is None)
    check("C3", "未知 key 返回 None",
          ai.find_portable_exe("no-such-tool", tools) is None)
    check("C4", "tools_root 不存在时返回 None",
          ai.find_portable_exe("blender", tools / "nope") is None)

# --------------------------------------------------------------- #
# D) check_tools 三级查找: PATH → 设置覆盖 → tools_root 便携版
# --------------------------------------------------------------- #
from app.utils import deps

with tempfile.TemporaryDirectory() as td:
    tools = Path(td)
    (tools / "colmap" / "COLMAP-3.9.1").mkdir(parents=True)
    fake_bat = tools / "colmap" / "COLMAP-3.9.1" / "COLMAP.bat"
    fake_bat.write_text("@echo off", encoding="ascii")

    s = settings_mod.PipelineSettings(tools_root=str(tools))
    report = {d.name: d for d in deps.check_tools(s)}
    check("D1", "tools_root 便携版 COLMAP 被体检命中(免 PATH)",
          report["colmap"].ok and str(fake_bat) in report["colmap"].detail,
          report["colmap"].detail)

    # 设置覆盖优先: gs_repo 指向不存在目录避免训练环境干扰
    fake_ff = tools / "ffmpeg.exe"
    fake_ff.write_bytes(b"MZ")
    s2 = settings_mod.PipelineSettings(tools_root=str(tools),
                                       ffmpeg_exe=str(fake_ff),
                                       gs_repo=str(tools / "gs"))
    report2 = {d.name: d for d in deps.check_tools(s2)}
    check("D2", "settings.ffmpeg_exe 覆盖被体检命中",
          report2["ffmpeg"].ok and str(fake_ff) in report2["ffmpeg"].detail,
          report2["ffmpeg"].detail)
    check("D3", "check_tools(settings=None) 向后兼容(仅 PATH)",
          isinstance(deps.check_tools(None), list))

    fr = deps.full_report(s2)
    kinds = {d.kind for d in fr}
    check("D4", "full_report(settings) 接受 settings 参数且类型齐全",
          kinds == {"python", "tool", "cuda"}, str(kinds))

# --------------------------------------------------------------- #
# E) 训练环境体检 + 初始化命令块
# --------------------------------------------------------------- #
with tempfile.TemporaryDirectory() as td:
    repo = Path(td) / "gs"
    s = settings_mod.PipelineSettings(gs_repo=str(repo))
    rep = {d.name: d for d in deps.check_training_env(s)}
    check("E1", "仓库缺失 → 「gaussian-splatting 仓库」行报缺失",
          rep.get("gaussian-splatting 仓库") is not None
          and rep["gaussian-splatting 仓库"].ok is False)
    check("E2", "venv 未建 → 「训练环境 venv」行报缺失",
          rep.get("训练环境 venv") is not None
          and rep["训练环境 venv"].ok is False)

    torch = deps.check_training_torch(s)
    check("E3", "无 venv 时 torch 探测快速返回「环境未创建」",
          torch.ok is False and "未创建" in torch.detail, torch.detail)

    (repo / "submodules").mkdir(parents=True)
    (repo / "train.py").write_text("", encoding="ascii")
    venv_py = repo / ".venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_bytes(b"MZ")
    rep2 = {d.name: d for d in deps.check_training_env(s)}
    check("E4", "train.py 就绪 → 仓库行 ok",
          rep2["gaussian-splatting 仓库"].ok is True)
    check("E5", ".venv/Scripts/python.exe 就绪 → venv 行 ok 且 detail 给出路径",
          rep2["训练环境 venv"].ok is True and str(venv_py) in rep2["训练环境 venv"].detail)

    cmd = deps.training_setup_text(s)
    check("E6", "初始化命令含 venv 创建", "venv" in cmd)
    check("E7", "初始化命令含 torch cu128 源(Blackwell 兼容)", "cu128" in cmd)
    for sub in ("diff-gaussian-rasterization", "simple-knn", "fused-ssim"):
        check(f"E8-{sub}", f"初始化命令含子模块 {sub}", sub in cmd)

# --------------------------------------------------------------- #
# F) AutoInstaller._install_portable 多源回退
# --------------------------------------------------------------- #
with tempfile.TemporaryDirectory() as td:
    tools = Path(td)
    inst = ai.AutoInstaller(tools_root=tools, allow_silent=True)
    tried = []

    def fake_download(url, dest, timeout=60):
        tried.append(url)
        if "bad" in url:
            raise requests.RequestException("模拟首源 403/超时")
        with zipfile.ZipFile(dest, "w") as zf:      # 次源产出合法 zip
            zf.writestr("blender/blender.exe", b"MZ-fake")

    inst._download = fake_download
    saved = ai.OFFICIAL_SOURCES["blender"]
    ai.OFFICIAL_SOURCES["blender"] = {
        "urls": ["https://bad.example/x.zip", "https://good.example/y.zip"],
        "exe_globs": saved["exe_globs"]}
    try:
        exe = inst._install_portable("blender")
    finally:
        ai.OFFICIAL_SOURCES["blender"] = saved
    check("F1", "首源失败自动回退次源(按序尝试)",
          tried == ["https://bad.example/x.zip", "https://good.example/y.zip"],
          str(tried))
    check("F2", "回退安装成功并定位到 exe",
          exe is not None and exe.name == "blender.exe", str(exe))
    check("F3", "zip 缓存已清理",
          not (tools / "blender" / "blender.zip").exists())

with tempfile.TemporaryDirectory() as td:
    tools = Path(td)
    inst2 = ai.AutoInstaller(tools_root=tools, allow_silent=True)

    def all_fail(url, dest, timeout=60):
        raise requests.RequestException("全源不可用")

    inst2._download = all_fail
    saved2 = ai.OFFICIAL_SOURCES["ffmpeg"]
    ai.OFFICIAL_SOURCES["ffmpeg"] = {
        "urls": ["https://a.example/1.zip", "https://b.example/2.zip"],
        "exe_globs": saved2["exe_globs"]}
    try:
        ret = inst2._install_portable("ffmpeg")
    finally:
        ai.OFFICIAL_SOURCES["ffmpeg"] = saved2
    check("F4", "全部源失败 → 返回 None 且不抛异常", ret is None)
    check("F5", "全源失败后 zip 缓存已清理",
          not (tools / "ffmpeg" / "ffmpeg.zip").exists())

with tempfile.TemporaryDirectory() as td:
    inst3 = ai.AutoInstaller(tools_root=Path(td))
    declined = []
    ret = inst3.ensure("blender", already_available=True)
    check("F6", "already_available=True 时 ensure 不安装直接 None", ret is None)
    ret2 = inst3._install_portable("blender")   # allow_silent=False 且无 consent_cb
    declined.append(ret2)
    check("F7", "未授权(无 consent_cb)时静默跳过返回 None", ret2 is None)

# --------------------------------------------------------------- #
# G) DependencyDialog GUI 冒烟: 镜像下拉动态生成 + 切换即保存
# --------------------------------------------------------------- #
try:
    from PySide6.QtWidgets import QApplication
    from app.views.main_window import DependencyDialog

    app = QApplication.instance() or QApplication(sys.argv)
    s = settings_mod.PipelineSettings(pip_mirror="tsinghua")
    dlg = DependencyDialog(s)
    items = [dlg.mirror.itemText(i) for i in range(dlg.mirror.count())]
    keys = [dlg.mirror.itemData(i) for i in range(dlg.mirror.count())]
    check("G1", "镜像下拉条目数 = PIP_MIRRORS 数(动态生成)",
          dlg.mirror.count() == len(settings_mod.PIP_MIRRORS))
    check("G2", "下拉键序与 PIP_MIRRORS 一致(userData=键)",
          keys == list(settings_mod.PIP_MIRRORS), str(keys))
    check("G3", "下拉显示名取自 PIP_MIRROR_LABELS",
          items == [settings_mod.PIP_MIRROR_LABELS.get(k, k)
                    for k in settings_mod.PIP_MIRRORS], str(items))
    check("G4", "初始选中项 = settings.pip_mirror",
          dlg.mirror.currentData() == "tsinghua")

    dlg.mirror.setCurrentIndex(keys.index("ustc"))
    check("G5", "切换镜像 → 即时写回 settings.pip_mirror",
          s.pip_mirror == "ustc", s.pip_mirror)

    rows = dlg.table.rowCount()
    kinds = [dlg.table.item(i, 1).text() for i in range(rows)]
    check("G6", "体检表含 4 类条目(Python 库/系统工具/GPU 驱动/训练环境)",
          {"Python 库", "系统工具", "GPU 驱动", "训练环境"}.issubset(set(kinds)),
          str(set(kinds)))
    check("G7", "表格含训练环境行(仓库+venv)",
          kinds.count("训练环境") >= 2)
    verdict = dlg.lbl_verdict.text()
    check("G8", "顶部结论行非空(够不够执行软件的直接回答)", len(verdict) > 10, verdict[:40])
    check("G9", "复制按钮存在且可点", dlg.btn_copy_cmd.isEnabled())
    check("G10", "工具多源安装按钮存在", dlg.btn_tools.text().find("多源") >= 0)

    # torch 后台线程结束前先手动回填, 验证 _on_torch 追加行逻辑
    d = deps.DepStatus(name="训练环境 torch+CUDA", kind="train", ok=True,
                       detail="模拟回填")
    before = dlg.table.rowCount()
    dlg._on_torch(d)
    check("G11", "_on_torch 回填表格末行并计入结论",
          dlg.table.rowCount() == before + 1
          and "torch" in dlg.table.item(before, 0).text())

    dlg.close()
    dlg.deleteLater()
    # 等待对话框派生的后台线程结束, 避免 Qt 关闭期崩溃(0xC0000409)
    from app.views import main_window as _mw
    for t in list(_mw._bg_threads):
        t.wait(10000)
except Exception as exc:  # noqa: BLE001
    check("G0", "GUI 冒烟未抛异常", False, repr(exc))

# --------------------------------------------------------------- #
# H) 冻结模式 pip 机制: 解释器解析 + --target 补装目录 + sys.path 注册
# --------------------------------------------------------------- #
from app.utils import paths as paths_mod


def _simulate_frozen(on: bool):
    if on:
        sys.frozen = True          # PyInstaller 在冻结壳内设置该属性
    elif hasattr(sys, "frozen"):
        delattr(sys, "frozen")


_was_frozen = deps.is_frozen()
try:
    _simulate_frozen(False)
    check("H1", "源码模式 pip_interpreter = 当前解释器",
          deps.pip_interpreter() == [sys.executable])
    cmd = deps.pip_cmd(["open3d"], "tsinghua")
    check("H2", "源码模式 pip 命令不含 --target(装进当前环境)",
          "--target" not in cmd and cmd[0] == sys.executable
          and "open3d" in cmd, str(cmd))
    check("H3", "pip 命令含 -i 与镜像 URL(键名不进命令)",
          "-i" in cmd and settings_mod.PIP_MIRRORS["tsinghua"] in cmd, str(cmd))
    cmd_none = deps.pip_cmd(["plyfile"], "none")
    check("H4", "none 镜像(官方直连)不加 -i", "-i" not in cmd_none, str(cmd_none))
    cmd_custom = deps.pip_cmd(["plyfile"], "aliyun", python_exe="X:/py.exe")
    check("H5", "显式 python_exe 优先", cmd_custom[0] == "X:/py.exe")

    _simulate_frozen(True)
    inter = deps.pip_interpreter()
    check("H6", "冻结模式解析到系统解释器(py 启动器或 python)",
          inter is not None and Path(inter[0]).exists()
          and inter[0] != sys.executable, str(inter))
    fcmd = deps.pip_cmd(["open3d"], "tsinghua")
    check("H7", "冻结模式 pip 命令带 --target 指向 python_pkgs/",
          "--target" in fcmd
          and str(paths_mod.external_pkgs_dir()) in fcmd, str(fcmd))
    check("H8", "冻结模式解释器不使用 exe 本身(sys.executable 不可作解释器)",
          fcmd[0] != sys.executable, fcmd[0])

    check("H9", "冻结模式 external_pkgs_dir = 用户主目录(Program Files 不可写)",
          paths_mod.external_pkgs_dir()
          == Path.home() / ".universal_scene_forge" / "python_pkgs",
          str(paths_mod.external_pkgs_dir()))
    before = list(sys.path)
    paths_mod.register_external_pkgs()
    paths_mod.register_external_pkgs()
    added = [p for p in sys.path if p not in before]
    check("H10", "register_external_pkgs 幂等追加(尾部) python_pkgs",
          added == [str(paths_mod.external_pkgs_dir())], str(added))
    sys.path[:] = before     # 还原, 不污染后续导入
finally:
    _simulate_frozen(_was_frozen)

# --------------------------------------------------------------- #
print()
if failures:
    print(f"FAILED: {len(failures)} 项未通过 -> {failures}")
    sys.exit(1)
print("ALL PASSED")
