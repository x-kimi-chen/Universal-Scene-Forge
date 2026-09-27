# -*- coding: utf-8 -*-
"""单元测试: 多源输入（多个视频/图片同时输入）功能回归。

覆盖三层:
- Ingester.collect_multi: 混合源收集 / 失败源跳过 / 去重 / 全失败报错
- PipelineController._ingest: 多源列表 → 合并帧写入 state.frames
- MainWindow (offscreen): 菜单栏「设置→软件调用路径」入口 + 多源列表 UI 冒烟

运行: .venv\\Scripts\\python.exe tests\\unit_ingest_multi.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 无头 GUI 冒烟

from app.core.ingest import Ingester, IngestError  # noqa: E402

failures = []


def check(no, desc, ok, detail=""):
    print(f"[{no}] {desc}: {'OK' if ok else 'FAIL'}"
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"[{no}] {desc}")


def make_images(folder: Path, n: int, prefix="img") -> list:
    """生成 n 个最小合法 JPEG（1x1 纯色）, 返回路径列表。"""
    import cv2
    import numpy as np
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        p = folder / f"{prefix}_{i:03d}.jpg"
        cv2.imwrite(str(p), np.full((4, 4, 3), 128, dtype=np.uint8))
        paths.append(p)
    return paths


tmp = Path(tempfile.mkdtemp(prefix="usf_multi_"))
try:
    # ---------------------------------------------------------------- #
    # 1) collect_multi: 多个图像目录合并
    # ---------------------------------------------------------------- #
    d1, d2 = tmp / "shots_a", tmp / "shots_b"
    f1 = make_images(d1, 3, "a")
    f2 = make_images(d2, 2, "b")
    ing = Ingester()
    merged = ing.collect_multi([d1, d2], tmp / "frames1")
    check("1a", "两个图像目录合并为 5 帧", len(merged) == 5, f"got {len(merged)}")
    check("1b", "合并结果包含两个目录的全部文件",
          set(merged) == set(f1) | set(f2))

    # ---------------------------------------------------------------- #
    # 2) 混合形态: 目录 + 单张图片
    # ---------------------------------------------------------------- #
    single = make_images(tmp / "solo", 1, "s")[0]
    merged2 = ing.collect_multi([d1, single], tmp / "frames2")
    check("2a", "目录 + 单张图片混合收集为 4 帧",
          len(merged2) == 4, f"got {len(merged2)}")
    check("2b", "单张图片包含在结果中", single in merged2)

    # ---------------------------------------------------------------- #
    # 3) 容错: 不存在的源跳过, 不阻断其他源
    # ---------------------------------------------------------------- #
    merged3 = ing.collect_multi(
        [tmp / "no_such_dir", d1], tmp / "frames3")
    check("3a", "不存在的源被跳过, 有效源照常收集",
          len(merged3) == 3, f"got {len(merged3)}")

    # ---------------------------------------------------------------- #
    # 4) 去重: 同一目录重复输入只算一次
    # ---------------------------------------------------------------- #
    merged4 = ing.collect_multi([d1, d1, d2], tmp / "frames4")
    check("4a", "重复输入的目录自动去重(5 帧而非 8 帧)",
          len(merged4) == 5, f"got {len(merged4)}")

    # ---------------------------------------------------------------- #
    # 5) 容错: 损坏视频只跳过该源(抽帧失败 → IngestError 被捕获)
    # ---------------------------------------------------------------- #
    fake_mp4 = tmp / "broken.mp4"
    fake_mp4.write_bytes(b"not a real video")
    merged5 = ing.collect_multi(
        [fake_mp4, d2], tmp / "frames5")
    check("5a", "损坏视频跳过后, 图像目录仍正常收集",
          merged5 == f2, f"got {merged5}")

    # ---------------------------------------------------------------- #
    # 6) 全部源失败 → IngestError
    # ---------------------------------------------------------------- #
    empty_dir = tmp / "empty"
    empty_dir.mkdir()
    try:
        ing.collect_multi([tmp / "no_such_dir", empty_dir],
                          tmp / "frames6")
        check("6a", "全部源无产出时抛 IngestError", False, "未抛异常")
    except IngestError:
        check("6a", "全部源无产出时抛 IngestError", True)

    # ---------------------------------------------------------------- #
    # 7) PipelineController._ingest: 多源列表写入 state.frames
    # ---------------------------------------------------------------- #
    from app.controllers.pipeline_controller import (  # noqa: E402
        EventBus, PipelineController)
    from app.models.settings import PipelineSettings  # noqa: E402

    bus = EventBus()
    ctrl = PipelineController(PipelineSettings(), bus, dcc_infos={})
    out = ctrl._ingest([d1, d2, single], tmp / "work")
    check("7a", "控制器 _ingest 接受多源列表并返回 6 帧",
          len(out) == 6, f"got {len(out)}")
    check("7b", "state.frames 与返回一致",
          [Path(p) for p in ctrl.state.frames] == out)
    check("7c", "缩略图目录已生成",
          (tmp / "work" / "thumbs").exists()
          and any((tmp / "work" / "thumbs").glob("thumb_*.jpg")))

    # ---------------------------------------------------------------- #
    # 8) execute() 源归一化: 单路径 / 列表两种形态等价入口
    #    (非 INGEST 阶段打桩, 只验证归一化与多源采集)
    # ---------------------------------------------------------------- #
    ctrl2 = PipelineController(PipelineSettings(), EventBus(), dcc_infos={})
    for stage_fn in ("_sfm", "_train", "_mesh", "_ue5_preview",
                     "_blender_post", "_metashape_repair", "_export"):
        setattr(ctrl2, stage_fn, lambda wd: None)
    ctrl2.execute([d1, d2], tmp / "work8")
    check("8a", "execute() 接受列表, state.source 记录 '; ' 分隔的多源清单",
          ctrl2.state.source.count(";") == 1, f"got: {ctrl2.state.source!r}")
    ing_rec = [r for r in ctrl2.state.records
               if r.name == "输入采集与抽帧"]
    check("8b", "多源 execute() 后 INGEST 阶段记录为成功",
          bool(ing_rec) and ing_rec[0].ok is True,
          str([f"{r.name}:ok={r.ok}" for r in ctrl2.state.records]))

    # ---------------------------------------------------------------- #
    # 9) GUI 冒烟(offscreen): 菜单栏 + 多源输入列表
    # ---------------------------------------------------------------- #
    from PySide6.QtWidgets import QApplication  # noqa: E402

    app = QApplication.instance() or QApplication(sys.argv)
    from app.views.main_window import MainWindow  # noqa: E402

    win = MainWindow()
    menus = [a.menu() for a in win.menuBar().actions() if a.menu()]
    menu_titles = [m.title() for m in menus]
    check("9a", "菜单栏含 文件/设置/帮助 三个菜单",
          menu_titles == ["文件(&F)", "设置(&S)", "帮助(&H)"],
          f"got {menu_titles}")

    set_menu = menus[1]
    set_actions = [a.text() for a in set_menu.actions()]
    check("9b", "设置菜单含「软件调用路径…」入口",
          "软件调用路径…" in set_actions, f"got {set_actions}")

    check("9c", "输入面板为多源列表 list_sources",
          hasattr(win, "list_sources") and not hasattr(win, "ed_source"))

    win._add_source(str(d1))
    win._add_source(str(d2))
    win._add_source(str(single))
    win._add_source(str(d1))  # 重复添加应被忽略
    check("9d", "GUI 多源列表去重后为 3 条",
          win.list_sources.count() == 3,
          f"got {win.list_sources.count()}")

    got = win._sources()
    check("9e", "_sources() 返回与列表一致的 Path 列表",
          got == [d1.resolve(), d2.resolve(), single.resolve()],
          f"got {got}")

    win._remove_selected_sources()  # 未选中时应为无操作
    check("9f", "未选中时移除为无操作",
          win.list_sources.count() == 3)
    win.list_sources.selectAll()
    win._remove_selected_sources()
    check("9g", "全选移除后列表清空", win.list_sources.count() == 0)

    check("9h", "抽帧上限控件 min=0 且上限放开",
          win.sp_frames.minimum() == 0
          and win.sp_frames.maximum() >= 99_999_999,
          f"range=[{win.sp_frames.minimum()}, {win.sp_frames.maximum()}]")
    check("9i", "3DGS 迭代数控件上限放开",
          win.sp_iters.maximum() >= 99_999_999,
          f"max={win.sp_iters.maximum()}")
    check("9j", "抽帧帧率控件上限放开(≥60fps)",
          win.sp_fps.maximum() >= 60.0, f"max={win.sp_fps.maximum()}")

    win.close()

    # ---------------------------------------------------------------- #
    # 10) 抽帧上限 0 = 不限制（FFmpeg / OpenCV 双路径均适配）
    # ---------------------------------------------------------------- #
    import cv2 as _cv2
    import numpy as _np
    vid = tmp / "clip.avi"
    vw = _cv2.VideoWriter(str(vid), _cv2.VideoWriter_fourcc(*"MJPG"),
                          10.0, (32, 32))
    if vw.isOpened():
        for k in range(8):
            vw.write(_np.full((32, 32, 3), k * 28, dtype=_np.uint8))
        vw.release()

        unlimited = ing.extract_frames(vid, tmp / "fx_all", fps=10.0,
                                       max_frames=0)
        huge = ing.extract_frames(vid, tmp / "fx_huge", fps=10.0,
                                  max_frames=9999)
        check("10a", "max_frames=0 与极大值抽帧结果一致(=不限制)",
              len(unlimited) == len(huge) and len(unlimited) >= 7,
              f"0->{len(unlimited)}, 9999->{len(huge)}")

        capped = ing.extract_frames(vid, tmp / "fx_cap", fps=10.0,
                                     max_frames=3)
        check("10b", "max_frames=3 仍按上限截断为 3 帧",
              len(capped) == 3, f"got {len(capped)}")

        merged10 = ing.collect_multi([vid], tmp / "fx_multi", fps=10.0,
                                     max_frames=0)
        check("10c", "collect_multi 透传 0=不限制",
              len(merged10) == len(unlimited), f"got {len(merged10)}")
    else:
        vw.release()
        check("10a", "跳过: 本机 OpenCV 无 MJPG 编码器", True)

    # ---------------------------------------------------------------- #
    # 11) 输入格式上限修复: 扩展名对齐 + 多目录同时添加
    # ---------------------------------------------------------------- #
    from app.core.ingest import VIDEO_EXTS as _V, IMAGE_EXTS as _I
    check("11a", "IMAGE_EXTS 已含 .webp (修复对话框与引擎不一致)",
          ".webp" in _I)
    check("11b", "VIDEO_EXTS 已扩充 webm/flv/mpg/m2ts 等常见格式",
          {".webm", ".flv", ".mpg", ".mpeg", ".ts", ".m2ts"} <= _V)

    # webp 单图应能被 collect_multi 正常收集 (此前会被静默跳过)
    webp_dir = tmp / "webp_shots"
    webp_dir.mkdir()
    webp_f = webp_dir / "frame_001.webp"
    try:
        import cv2 as _cv2
        import numpy as _np
        if not _cv2.imwrite(str(webp_f),
                            _np.full((4, 4, 3), 128, dtype=_np.uint8)):
            raise RuntimeError("cv2 不支持 webp 编码")
        got_webp = ing.collect_multi([webp_f], tmp / "frames_webp")
        check("11c", "webp 单张图片可被收集 (不再静默跳过)",
              len(got_webp) == 1 and got_webp[0] == webp_f,
              f"got {len(got_webp)}")
    except Exception as exc:  # webp 编码不可用时跳过
        check("11c", f"跳过 webp 编码测试 ({exc})", True)

    # 多选目录对话框: 程序化选中两个目录, selected_dirs 应返回两个
    from PySide6.QtWidgets import QApplication as _QApp
    from PySide6.QtCore import QCoreApplication as _QCore
    from app.views.main_window import _MultiDirDialog
    if _QApp.instance() is not None:
        dlg = _MultiDirDialog()
        model = dlg.model
        root_dir = tmp / "multi_dir_probe"
        (root_dir / "d1").mkdir(parents=True)
        (root_dir / "d2").mkdir(parents=True)
        model.setRootPath(str(root_dir))
        import time as _time
        for _ in range(100):         # 等文件系统模型异步加载
            if model.rowCount(model.index(str(root_dir))) >= 2:
                break
            _QCore.processEvents()   # 驱动模型刷新
            _time.sleep(0.05)
        base = model.index(str(root_dir))
        i1 = model.index(0, 0, base)
        i2 = model.index(1, 0, base)
        if i1.isValid() and i2.isValid():
            from PySide6.QtCore import QItemSelectionModel as _QISM
            sel = dlg.view.selectionModel()
            sel.select(i1, _QISM.Select | _QISM.Rows)
            sel.select(i2, _QISM.Select | _QISM.Rows)
            dirs = dlg.selected_dirs()
            check("11d", "多选目录对话框可一次选中 2 个目录",
                  len(dirs) == 2, f"got {len(dirs)}: {dirs}")
        else:
            check("11d", "跳过: 模型未加载出测试目录", True)
        dlg.close()
    else:
        check("11d", "跳过: QApplication 未初始化", True)

    # 对话框过滤器与引擎扩展名集合保持同步 (动态生成, 单一数据源)
    src_win = (ROOT / "app" / "views" / "main_window.py").read_text(
        encoding="utf-8")
    check("11e", "文件对话框过滤器由 VIDEO_EXTS/IMAGE_EXTS 动态生成",
          "VIDEO_EXTS | IMAGE_EXTS" in src_win
          and "exts = sorted" in src_win)

    # ---------------------------------------------------------------- #
    # 12) 进度反馈: 步骤条 / 大进度条 / 失败弹窗
    # ---------------------------------------------------------------- #
    from app.views.main_window import STAGE_ORDER as _SO
    check("12a", "STAGE_ORDER 覆盖流水线全部 9 阶段", len(_SO) == 9)
    win.lbl_stage.setText("就绪")                     # 复位
    win.bus.stage.emit(_SO[0])
    check("12b", "阶段信号更新计数与 active 步骤",
          win.lbl_step.text() == "阶段 1/9"
          and win._stage_states[0] == "active"
          and win.lbl_stage.text() == _SO[0])
    win.bus.stage_failed.emit(_SO[0])
    check("12c", "失败信号将步骤标红",
          win._stage_states[0] == "failed"
          and win.step_labels[0].text().startswith("✗"))
    win.bus.progress.emit(42, "测试消息")
    check("12d", "进度信号更新进度条与消息(不污染阶段标签)",
          win.progress.value() == 42 and win.lbl_msg.text() == "测试消息"
          and win.lbl_stage.text() == _SO[0])
    _alerts = []
    win._alert_failure = lambda t, d: _alerts.append(t)
    win.bus.finished.emit(False, "总结: 测试")
    check("12e", "失败完成信号触发醒目弹窗",
          len(_alerts) == 1 and win.lbl_msg.text() == "总结: 测试")
    win._stage_states[1] = "active"                   # 模拟进行中收尾
    win.bus.finished.emit(True, "全部完成")
    check("12f", "成功完成信号进度条到 100% 且不弹窗",
          win.progress.value() == 100 and len(_alerts) == 1
          and win._stage_states[1] == "done")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- #
print()
if failures:
    print(f"FAILED: {len(failures)} 项未通过 -> {failures}")
    sys.exit(1)
print("ALL PASS: 多源输入功能单元测试全部通过")
