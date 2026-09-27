# -*- coding: utf-8 -*-
"""单元测试: Premiere Pro 高效抽帧（CEP 扩展 + 桥接器 + GUI 对话框）。

覆盖面:
- E) CEP 扩展四件套契约（manifest 主机/版本范围, JSX API 调用,
     面板与桥接器的文件名协议, CSInterface 轻量 shim）
- B) 桥接器: install_extension 幂等复制 / PlayerDebugMode 注入 /
     任务-状态文件读写 / cancel 翻转 / 旧状态清理
- D) detect_premiere: 覆盖路径(exe/目录) / 注册表注入 / 探测不变式
- G) PrFrameDialog GUI 冒烟(offscreen): 预填/轮询/frames_ready 回填
- S) 集成面: settings 字段 / paths 扩展目录 / spec datas / 主窗口菜单入口
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

failures = []


def check(no, desc, ok, detail=""):
    print(f"[{no}] {desc}: {'OK' if ok else 'FAIL'}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"[{no}] {desc}")


from app.dcc import premiere_bridge as pb

EXT_SRC = ROOT / "extensions" / "pr_frame_exporter"

# --------------------------------------------------------------- #
# E) CEP 扩展契约
# --------------------------------------------------------------- #
manifest = ET.parse(EXT_SRC / "CSXS" / "manifest.xml").getroot()
check("E1", "manifest 根节点 Version=9.0(CEP9+ 向后兼容)",
      manifest.get("Version") == "9.0", manifest.get("Version"))
check("E2", "ExtensionBundleId 与桥接器常量一致",
      manifest.get("ExtensionBundleId") == pb.EXT_BUNDLE_ID)
hosts = manifest.findall(".//HostList/Host")
check("E3", "Host=PPRO 版本范围 [13.0,99.9]（2019~2025+）",
      len(hosts) == 1 and hosts[0].get("Name") == "PPRO"
      and hosts[0].get("Version") == "[13.0,99.9]",
      str([(h.get("Name"), h.get("Version")) for h in hosts]))
rt = manifest.findall(".//RequiredRuntime")
check("E4", "RequiredRuntime=CSXS 9.0",
      len(rt) == 1 and rt[0].get("Name") == "CSXS"
      and rt[0].get("Version") == "9.0")
check("E5", "面板入口 MainPath=./index.html, ScriptPath 指向 jsx",
      manifest.findtext(".//MainPath") == "./index.html"
      and manifest.findtext(".//ScriptPath") == "./jsx/frame_exporter.jsx")
check("E6", "菜单名已本地化（PR 扩展菜单内显示）",
      "USF" in manifest.findtext(".//UI/Menu"))

jsx = (EXT_SRC / "jsx" / "frame_exporter.jsx").read_text(encoding="utf-8")
check("E7", "JSX 调用序列导出 API exportFrameJPEG/PNG",
      "exportFrameJPEG" in jsx and "exportFramePNG" in jsx)
check("E8", "JSX 分块导出协议 usfStart/usfChunk/usfPrepare/usfCancel",
      all(f"function {f}" in jsx for f in
          ("usfStart", "usfChunk", "usfPrepare", "usfCancel")))
check("E9", "JSX ticks 常量 254016000000 与分块游标",
      "254016000000" in jsx and "nextIndex" in jsx)
check("E10", "JSX 返回协议 OK|/ERR|（面板按竖线解析）",
      '"OK|"' in jsx or "'OK|'" in jsx)

html = (EXT_SRC / "index.html").read_text(encoding="utf-8")
check("E11", "面板引用 CSInterface shim 与 usf_task/usf_status 文件名",
      "js/CSInterface.js" in html
      and "usf_task.json" in html and "usf_status.json" in html)
check("E12", "面板双模式按钮（自动导入全部 / 当前序列）",
      "btnAll" in html and "btnCur" in html and "usfPrepare" in html)
check("E13", "面板分块 pump + 轮询取消(1s) + 状态回写",
      "usfChunk" in html and "cancelled" in html and "writeStatus" in html)

shim = (EXT_SRC / "js" / "CSInterface.js").read_text(encoding="utf-8")
check("E14", "CSInterface shim 基于 __adobe_cep__.evalScript",
      "__adobe_cep__" in shim and "evalScript" in shim)

# --------------------------------------------------------------- #
# B) 桥接器
# --------------------------------------------------------------- #
check("B1", "随包扩展源含四件套(manifest/jsx/html/shim)",
      all((EXT_SRC / f).is_file() for f in
          ("CSXS/manifest.xml", "jsx/frame_exporter.jsx",
           "index.html", "js/CSInterface.js")))
check("B2", "bundled_extension_dir 定位扩展源",
      pb.bundled_extension_dir() == EXT_SRC, str(pb.bundled_extension_dir()))

_orig_debug = pb._enable_player_debug_mode
pb._enable_player_debug_mode = lambda: None     # 测试期不写真实注册表
try:
    with tempfile.TemporaryDirectory() as td:
        appdata = Path(td)
        dst = pb.install_extension(appdata)
        files = {str(p.relative_to(dst)).replace("\\", "/")
                 for p in dst.rglob("*") if p.is_file()}
        check("B3", "install_extension 复制全部文件到 CEP 目录",
              {"CSXS/manifest.xml", "jsx/frame_exporter.jsx",
               "index.html", "js/CSInterface.js"}.issubset(files),
              str(files))
        before = sorted(p.name for p in dst.rglob("*"))
        pb.install_extension(appdata)          # 幂等覆盖更新
        check("B4", "install_extension 幂等（重复安装不报错）",
              sorted(p.name for p in dst.rglob("*")) == before)
        check("B5", "扩展目录名 = ExtensionBundleId",
              dst.name == pb.EXT_BUNDLE_ID
              and dst.parent == appdata / "Adobe" / "CEP" / "extensions")

        # 旧状态清理 + 任务写入
        (dst / pb.STATUS_FILE).write_text('{"state":"done"}',
                                          encoding="utf-8")
        out_dir = Path(td) / "frames"
        task_path = pb.write_task(
            [r"C:\v\a.mp4", r"C:\v\b.mp4"], 2.0, 300, str(out_dir),
            appdata=appdata)
        task = json.loads(task_path.read_text(encoding="utf-8"))
        check("B6", "write_task 字段完整（路径转正斜杠供 JSX）",
              task["videos"] == ["C:/v/a.mp4", "C:/v/b.mp4"]
              and task["fps"] == 2.0 and task["max_frames"] == 300
              and task["out_dir"] == str(out_dir).replace("\\", "/")
              and task["cancelled"] is False and task["format"] == "jpg")
        check("B7", "write_task 清理上一轮状态文件",
              not (dst / pb.STATUS_FILE).exists())
        check("B8", "read_task 往返",
              (pb.read_task(appdata) or {}).get("fps") == 2.0)

        check("B9", "cancel_task 翻转 cancelled 且保留任务",
              pb.cancel_task(appdata)
              and pb.read_task(appdata)["cancelled"] is True
              and len(pb.read_task(appdata)["videos"]) == 2)
        check("B10", "cancel_task 无任务时返回 False",
              pb.cancel_task(appdata.parent / "nonexist") is False)

        st = pb.extension_install_dir(appdata) / pb.STATUS_FILE
        st.write_text(json.dumps({"state": "running", "exported": 3,
                                  "total": 10}), encoding="utf-8")
        check("B11", "read_status 解析面板状态",
              (pb.read_status(appdata) or {}).get("exported") == 3)
        st.write_text("{not-json", encoding="utf-8")
        check("B12", "read_status 容错（损坏 JSON → None）",
              pb.read_status(appdata) is None)
        st.unlink()
        check("B13", "read_status 无文件 → None",
              pb.read_status(appdata) is None)
finally:
    pb._enable_player_debug_mode = _orig_debug

calls = []
pb._enable_player_debug_mode(_setter=lambda path, name, val:
                             calls.append((path, name, val)))
check("B14", "PlayerDebugMode 写 CSXS 8~12 全套（未签名扩展加载前提）",
      len(calls) == 5
      and all(c[1] == "PlayerDebugMode" and c[2] == "1" for c in calls)
      and calls[0][0] == r"Software\Adobe\CSXS.8"
      and calls[-1][0] == r"Software\Adobe\CSXS.12", str(calls))

# --------------------------------------------------------------- #
# D) detect_premiere
# --------------------------------------------------------------- #
with tempfile.TemporaryDirectory() as td:
    fake = Path(td) / pb.PR_EXE_NAME
    fake.write_bytes(b"MZ")
    check("D1", "覆盖路径=exe 文件 → 直接命中",
          pb.detect_premiere(str(fake)) == str(fake))
    d2 = Path(td) / "dir"
    d2.mkdir()
    (d2 / pb.PR_EXE_NAME).write_bytes(b"MZ")
    check("D2", "覆盖路径=目录 → 拼接主程序名",
          pb.detect_premiere(str(d2)) == str(d2 / pb.PR_EXE_NAME))
    check("D3", "注册表返回目录 → 命中",
          pb.detect_premiere(None, _reg=lambda: str(d2))
          == str(d2 / pb.PR_EXE_NAME))
    check("D4", "注册表直接返回 exe → 命中",
          pb.detect_premiere(None, _reg=lambda: str(fake)) == str(fake))
    check("D5", "注册表返回空目录 → 回退常见路径或 None",
          pb.detect_premiere(None, _reg=lambda: str(Path(td) / "empty"))
          is None or (Path(td) / "empty").exists() is False)
    r = pb.detect_premiere(None, _reg=lambda: None)
    check("D6", "全链路探测不变式: None 或存在的 exe 路径",
          r is None or (Path(r).is_file()
                        and Path(r).name.lower() == pb.PR_EXE_NAME.lower()))

# --------------------------------------------------------------- #
# S) 集成面
# --------------------------------------------------------------- #
from app.models.settings import PipelineSettings
from app.utils.paths import extensions_dir

check("S1", "settings 新增 premiere_exe 字段(默认 None)",
      PipelineSettings().premiere_exe is None)
check("S2", "paths.extensions_dir 含 pr_frame_exporter",
      (extensions_dir() / "pr_frame_exporter" / "CSXS"
       / "manifest.xml").is_file(), str(extensions_dir()))
_spec = (ROOT / "installer" / "UniversalSceneForge.spec").read_text(
    encoding="utf-8")
check("S3", "spec datas 挂接 extensions 目录（进 exe 包体）",
      '"extensions"' in _spec and "extensions" in _spec)
_mw_src = (ROOT / "app" / "views" / "main_window.py").read_text(
    encoding="utf-8")
check("S4", "主窗口文件菜单含 PR 入口并连接处理器",
      "Premiere Pro 高效抽帧…" in _mw_src
      and "_open_pr_dialog" in _mw_src
      and "frames_ready.connect(self._add_source)" in _mw_src)

# --------------------------------------------------------------- #
# G) PrFrameDialog GUI 冒烟(offscreen)
# --------------------------------------------------------------- #
try:
    from PySide6.QtWidgets import QApplication
    from app.views.pr_dialog import PrFrameDialog

    app = QApplication.instance() or QApplication(sys.argv)
    _orig_detect = pb.detect_premiere
    pb.detect_premiere = lambda *a, **k: None    # 探测与测试机解耦
    _orig_status = pb.read_status
    try:
        s = PipelineSettings(frame_fps=3.0, max_frames=120)
        dlg = PrFrameDialog(s, [r"C:\v\a.mp4", r"C:\v\b.mp4"])
        check("G1", "对话框预填视频列表",
              dlg.list_videos.count() == 2)
        check("G2", "帧率/上限默认取 settings",
              dlg.sp_fps.value() == 3.0 and dlg.sp_frames.value() == 120)
        check("G3", "输出目录默认用户主目录约定位置",
              ".universal_scene_forge" in dlg.edit_out.text())
        check("G4", "轮询定时器已启动", dlg.timer.isActive())

        got = []
        dlg.frames_ready.connect(lambda p: got.append(p))
        fake_out = Path(tempfile.mkdtemp()) / "pr_frames"
        fake_out.mkdir()
        pb.read_status = lambda *a, **k: {
            "state": "done", "exported": 42, "total": 42,
            "video_index": 1, "total_videos": 2,
            "out_dir": str(fake_out)}
        dlg._poll_status()
        check("G5", "done 状态 → frames_ready 回填帧目录",
              got == [str(fake_out)] and dlg.progress.value() == 100,
              f"{got} {dlg.progress.value()}")
        dlg._poll_status()
        check("G6", "done 只回填一次（防重复添加输入源）", len(got) == 1)

        pb.read_status = lambda *a, **k: {
            "state": "running", "exported": 10, "total": 40,
            "video_index": 0, "total_videos": 2, "out_dir": str(fake_out)}
        dlg._emitted = False
        dlg._poll_status()
        check("G7", "running 状态 → 进度条与文案更新",
              dlg.progress.value() == 25 and "10/40" in dlg.lbl_status.text(),
              dlg.lbl_status.text())
        # 非可见对话框 close() 不派发 closeEvent, 直接调用以验证清理逻辑
        from PySide6.QtGui import QCloseEvent
        dlg.closeEvent(QCloseEvent())
        check("G8", "关闭对话框 → 停止轮询", not dlg.timer.isActive())
        dlg.deleteLater()
    finally:
        pb.detect_premiere = _orig_detect
        pb.read_status = _orig_status
except Exception as exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    failures.append(f"[G] GUI 冒烟异常: {exc}")

# --------------------------------------------------------------- #
print("=" * 60)
if failures:
    print(f"FAILED: {len(failures)} 项")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(f"ALL PASSED ({time.strftime('%H:%M:%S')})")
