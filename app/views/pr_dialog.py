"""Premiere Pro 高效抽帧对话框。

流程: 选择视频（默认取主窗口输入源中的视频）→ 设置帧率/上限/输出目录 →
「生成任务并启动 Premiere」（安装 CEP 扩展 + 写任务 + 启动 PR）→
在 PR 内的「USF 抽帧导出」面板点击①一键导出 → 本对话框轮询状态文件 →
完成后把帧目录自动回填为主窗口输入源。

与 premiere_bridge.py 的分工: 本文件只做视图与轮询, 探测/安装/任务 IO
全部在桥接模块（可单测锁定）。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
)

from app.dcc import premiere_bridge as pb
from app.models.settings import PipelineSettings
from app.utils.logger import get_logger

log = get_logger("PRDIALOG")

POLL_MS = 2000


class PrFrameDialog(QDialog):
    """PR 抽帧任务生成 + 状态轮询。"""

    frames_ready = Signal(str)   # 导出完成 → 帧目录（主窗口接住后加入输入源）

    def __init__(self, settings: PipelineSettings, videos: List[str],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = settings
        self._emitted = False
        self.setWindowTitle("Premiere Pro 高效抽帧")
        self.resize(560, 640)
        self._build_ui(videos)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_status)
        self.timer.start(POLL_MS)

    # ------------------------------------------------------------------ #
    def _build_ui(self, videos: List[str]):
        root = QVBoxLayout(self)

        tip = QLabel(
            "用 Premiere Pro 解码器批量导帧（比 ffmpeg 逐帧 seek 更稳, "
            "还可先剪辑/调色再导出）。流程:\n"
            "1. 点击下方「生成任务并启动 Premiere」;\n"
            "2. 在 Premiere 中打开 窗口 → 扩展 → USF 抽帧导出 面板;\n"
            "3. 点击面板「① 自动导入并导出全部视频」;\n"
            "4. 导出完成后帧目录会自动加入主程序输入源。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#6b7280;")
        root.addWidget(tip)

        # ---- 视频列表 ----
        box_v = QGroupBox("待抽帧视频")
        vlay = QVBoxLayout(box_v)
        self.list_videos = QListWidget()
        self.list_videos.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_videos.setMinimumHeight(110)
        for v in videos:
            self.list_videos.addItem(v)
        vlay.addWidget(self.list_videos)
        row_v = QHBoxLayout()
        btn_add = QPushButton("添加视频…")
        btn_add.clicked.connect(self._add_videos)
        btn_rm = QPushButton("移除所选")
        btn_rm.clicked.connect(self._remove_selected)
        row_v.addWidget(btn_add)
        row_v.addWidget(btn_rm)
        wrap_v = QWidget()
        wrap_v.setLayout(row_v)
        vlay.addWidget(wrap_v)
        root.addWidget(box_v)

        # ---- 参数 ----
        box_p = QGroupBox("导出参数")
        form = QFormLayout(box_p)
        self.sp_fps = QDoubleSpinBox()
        self.sp_fps.setDecimals(1)
        self.sp_fps.setRange(0.1, 999.0)
        self.sp_fps.setValue(self.settings.frame_fps)
        form.addRow("抽帧帧率 (fps):", self.sp_fps)
        self.sp_frames = QSpinBox()
        self.sp_frames.setRange(0, 999_999_999)
        self.sp_frames.setGroupSeparatorShown(True)
        self.sp_frames.setValue(self.settings.max_frames)
        self.sp_frames.setToolTip("0 = 不限制")
        form.addRow("抽帧上限 (0=不限):", self.sp_frames)

        row_out = QHBoxLayout()
        self.edit_out = QLineEdit(
            str(Path.home() / ".universal_scene_forge" / "pr_frames"))
        btn_out = QPushButton("浏览…")
        btn_out.clicked.connect(self._pick_out_dir)
        row_out.addWidget(self.edit_out, 1)
        row_out.addWidget(btn_out)
        wrap_o = QWidget()
        wrap_o.setLayout(row_out)
        form.addRow("帧输出目录:", wrap_o)
        root.addWidget(box_p)

        # ---- Premiere 路径 ----
        box_pr = QGroupBox("Premiere Pro")
        lay_pr = QVBoxLayout(box_pr)
        row_pr = QHBoxLayout()
        detected = pb.detect_premiere(self.settings.premiere_exe)
        self.edit_pr = QLineEdit(detected or "")
        self.edit_pr.setPlaceholderText(
            "自动探测为空 → 手动指定 Adobe Premiere Pro.exe")
        btn_pr = QPushButton("浏览…")
        btn_pr.clicked.connect(self._pick_pr)
        row_pr.addWidget(self.edit_pr, 1)
        row_pr.addWidget(btn_pr)
        wrap_pr = QWidget()
        wrap_pr.setLayout(row_pr)
        lay_pr.addWidget(wrap_pr)
        row_stop = QHBoxLayout()
        btn_stop = QPushButton("发送停止指令")
        btn_stop.setToolTip("通知 PR 面板停止当前导出")
        btn_stop.clicked.connect(self._cancel)
        row_stop.addWidget(btn_stop)
        row_stop.addStretch(1)
        wrap_stop = QWidget()
        wrap_stop.setLayout(row_stop)
        lay_pr.addWidget(wrap_stop)
        root.addWidget(box_pr)

        # ---- 动作 ----
        self.btn_launch = QPushButton("生成任务并启动 Premiere")
        self.btn_launch.setMinimumHeight(38)
        self.btn_launch.setStyleSheet(
            "font-weight:bold; background:#2d6dcc; color:white;"
            " border-radius:6px;")
        self.btn_launch.clicked.connect(self._launch)
        root.addWidget(self.btn_launch)

        # ---- 状态 ----
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)
        self.lbl_status = QLabel("尚未启动（启动后此处显示导出进度）")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)
        self.browser = QTextBrowser()
        self.browser.setMinimumHeight(120)
        root.addWidget(self.browser, 1)

    # ------------------------------------------------------------------ #
    def _add_videos(self):
        files, _ = QFileDialog.getOpenFileUrls(
            self, "添加视频", Path.home(),
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.m4v *.webm *.wmv *.mpg "
            "*.mpeg *.ts *.m2ts *.mts *.flv);;所有文件 (*.*)")
        for u in files:
            p = u.toLocalFile()
            if p and not self._has_item(p):
                self.list_videos.addItem(p)

    def _has_item(self, text: str) -> bool:
        return any(self.list_videos.item(i).text() == text
                   for i in range(self.list_videos.count()))

    def _remove_selected(self):
        for item in self.list_videos.selectedItems():
            self.list_videos.takeItem(self.list_videos.row(item))

    def _pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择帧输出目录",
                                              self.edit_out.text() or "")
        if d:
            self.edit_out.setText(d)

    def _pick_pr(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "选择 Adobe Premiere Pro.exe", r"C:\Program Files\Adobe",
            "Premiere (Adobe Premiere Pro.exe)")
        if f:
            self.edit_pr.setText(f)

    # ------------------------------------------------------------------ #
    def _videos(self) -> List[str]:
        return [self.list_videos.item(i).text()
                for i in range(self.list_videos.count())]

    def _log(self, msg: str):
        self.browser.append(msg)

    def _launch(self):
        videos = self._videos()
        if not videos:
            QMessageBox.warning(self, "提示", "请先添加待抽帧的视频。")
            return
        exe = pb.detect_premiere(self.edit_pr.text().strip() or None)
        if not exe:
            QMessageBox.warning(
                self, "未找到 Premiere Pro",
                "未探测到 Adobe Premiere Pro。\n"
                "请安装后重试, 或在上方手动指定 Adobe Premiere Pro.exe 路径。")
            return
        if self.edit_pr.text().strip() != (self.settings.premiere_exe or ""):
            self.settings.premiere_exe = self.edit_pr.text().strip()
            try:
                self.settings.save()
            except OSError as exc:
                log.warning("settings 保存失败: %s", exc)

        out_dir = Path(self.edit_out.text().strip())
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            task = pb.write_task(videos, self.sp_fps.value(),
                                 self.sp_frames.value(), str(out_dir))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "任务生成失败", f"写任务文件失败:\n{exc}")
            return
        self._emitted = False
        self._log(f"任务已生成: {task}")
        if pb.launch_premiere(exe):
            self._log(f"已启动 Premiere ({exe})。")
            self.lbl_status.setText(
                "等待面板导出… 在 PR 中: 窗口 → 扩展 → USF 抽帧导出 → 点「①」")
        else:
            self.lbl_status.setText("Premiere 启动失败, 可手动打开。")

    def _cancel(self):
        if pb.cancel_task():
            self._log("已发送停止指令（面板将在 1 秒内停止）")

    # ------------------------------------------------------------------ #
    def _poll_status(self):
        st = pb.read_status()
        if not st:
            return
        state = st.get("state", "")
        exported = int(st.get("exported", 0) or 0)
        total = int(st.get("total", 0) or 0)
        vi = int(st.get("video_index", 0) or 0)
        nv = int(st.get("total_videos", 0) or 0)
        if total > 0:
            self.progress.setValue(min(100, exported * 100 // total))
        if state == "running":
            self.lbl_status.setText(
                f"导出中 · 素材 {vi + 1}/{nv} · 帧 {exported}/{total}")
        elif state == "done" and not self._emitted:
            self._emitted = True
            out = st.get("out_dir", "")
            self.progress.setValue(100)
            self.lbl_status.setText(f"完成 · 共 {exported} 帧 → {out}")
            self._log(f"PR 抽帧完成: {exported} 帧 → {out}")
            if out and Path(out).is_dir():
                self.frames_ready.emit(out)
        elif state == "error":
            self.lbl_status.setText(f"面板报错: {st.get('error', '')}")
            self._log(f"[错误] {st.get('error', '')}")
        elif state == "cancelled":
            self.lbl_status.setText("已停止（可在 PR 面板重新开始）")

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
