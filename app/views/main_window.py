"""Universal Scene Forge 主窗口（PySide6）。

布局:
┌─ 菜单栏: 文件 / 设置(软件调用路径·依赖体检) / 帮助 ─────────┐
┌──────────────┬──────────────────────────────┐
│ 输入源面板    │ 进度反馈区                    │
│ 多源列表     │ 阶段名+进度条+8段步骤条+消息   │
│ (视频/图片混合)│──────────────────────────────│
│              │ DCC 状态指示灯矩阵            │
│ 参数区       │──────────────────────────────│
│ [开始][取消]  │ 日志控制台                    │
└──────────────┴──────────────────────────────┘
MVC: 本文件只做视图/信号订阅, 业务在 PipelineController。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QDir, QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDoubleSpinBox, QFileDialog, QFileSystemModel, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTreeView,
    QVBoxLayout, QWidget,
)

from app import APP_NAME, __version__
from app.controllers.pipeline_controller import EventBus, PipelineController
from app.dcc import extension_manager
from app.dcc.auto_installer import AutoInstaller, OFFICIAL_SOURCES
from app.dcc.registry_probe import DccDetector
from app.models.project import Stage
from app.models.settings import PIP_MIRRORS, PIP_MIRROR_LABELS, PipelineSettings
from app.utils import deps
from app.utils.logger import get_logger

log = get_logger("GUI")

# 流水线 8 阶段（与 PipelineController.execute 的 stages 顺序一致）
STAGE_ORDER = [Stage.INGEST.value, Stage.SFM.value, Stage.TRAIN.value,
               Stage.MESH.value, Stage.UE5_PREVIEW.value,
               Stage.DCC_POST.value, Stage.REPAIR.value, Stage.EXPORT.value]
STAGE_SHORT = ["采集", "SfM", "训练", "网格", "UE5", "后处理", "修复", "导出"]

# 步骤条状态样式: (标记, 样式)
STEP_STYLES = {
    "todo":   ("○", "color:#9ca3af; border:1px solid #4b5563; background:transparent;"),
    "active": ("●", "color:#fbbf24; border:1px solid #f59e0b; background:rgba(245,158,11,0.15);"),
    "done":   ("✓", "color:#22c55e; border:1px solid #22c55e; background:rgba(34,197,94,0.12);"),
    "failed": ("✗", "color:#ef4444; border:1px solid #ef4444; background:rgba(239,68,68,0.15);"),
    "skipped": ("◇", "color:#3b82f6; border:1px solid #3b82f6; background:rgba(59,130,246,0.12);"),
}

LED_COLORS = {
    "ok": "#22c55e",      # 绿: 成功
    "busy": "#f59e0b",     # 黄: 执行中
    "error": "#ef4444",    # 红: 失败
    "off": "#6b7280",      # 灰: 未安装
    "skipped": "#3b82f6",  # 蓝: 主动跳过
}

# 依赖体检表格「类型」列显示名
DEP_KIND_LABELS = {"python": "Python 库", "tool": "系统工具",
                   "cuda": "GPU 驱动", "train": "训练环境",
                   "ext": "DCC 扩展"}

# 模块级后台线程登记: 防止对话框先于线程销毁导致 QThread 被 GC 崩溃
_bg_threads: set = set()

# (探测 key, 显示名, settings 字段名) — 与 PipelineController 共用
# （定义移至 registry_probe, 此处转发导入保持既有引用兼容）
from app.dcc.registry_probe import DCC_PATH_FIELDS  # noqa: E402


class _QtLogBridge(QObject):
    """logging → GUI 的线程安全通道（工作线程 emit, 主线程 append）。"""
    logged = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._handler = None

    def attach(self, target):
        from app.utils.logger import install_qt_handler
        self.logged.connect(target)
        self._handler = install_qt_handler(
            lambda lvl, msg: self.logged.emit(lvl, msg))

    def detach(self):
        if self._handler is not None:
            from app.utils.logger import logging
            logging.getLogger().removeHandler(self._handler)
            self._handler = None


class PipelineThread(QThread):
    done = Signal(bool, str)

    def __init__(self, controller: PipelineController,
                 source: "Path | list[Path]", work_dir: Path, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.source = source
        self.work_dir = work_dir

    def run(self):
        try:
            ok = self.controller.execute(self.source, self.work_dir)
            self.done.emit(ok, "")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, str(exc))


class DccLed(QLabel):
    def __init__(self, name: str):
        super().__init__()
        self._name = name
        self.set_state("off")

    def set_state(self, state: str):
        color = LED_COLORS.get(state, "#6b7280")
        self.setText(f'<span style="color:{color};font-size:16px;">●</span> {self._name}')
        self.setToolTip(f"{self._name}: {state}")


class DependencyDialog(QDialog):
    """依赖体检: 表格 + 一键安装（pip 多镜像 + 工具多源回退 + 训练环境探测）。

    - pip 镜像下拉由 PIP_MIRRORS/PIP_MIRROR_LABELS 动态生成, 选择即保存;
    - 工具安装走 AutoInstaller 多源回退（Blender 3 源 / FFmpeg 2 源 / COLMAP）,
      成功后回写 settings 供流水线直接调用;
    - 训练环境 torch+CUDA 探测慢（子进程导入）, 放后台线程, 结果回填表格末行。
    """

    def __init__(self, settings: PipelineSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("依赖体检与一键安装")
        self.resize(880, 580)
        layout = QVBoxLayout(self)

        self.lbl_verdict = QLabel("")
        self.lbl_verdict.setWordWrap(True)
        layout.addWidget(self.lbl_verdict)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["组件", "类型", "状态", "修复建议"])
        self.table.horizontalHeader().stretchLastSection = True
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 320)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        row.addWidget(QLabel("pip 镜像:"))
        self.mirror = QComboBox()
        for key in PIP_MIRRORS:          # 数据源唯一: 增删下载源无需改本类
            self.mirror.addItem(PIP_MIRROR_LABELS.get(key, key), key)
        if self.settings.pip_mirror in PIP_MIRRORS:
            self.mirror.setCurrentIndex(
                list(PIP_MIRRORS).index(self.settings.pip_mirror))
        self.mirror.currentIndexChanged.connect(self._save_mirror)
        row.addWidget(self.mirror)
        self.btn_pip = QPushButton("安装缺失 Python 库")
        self.btn_pip.clicked.connect(self._pip_install)
        row.addWidget(self.btn_pip)
        self.btn_tools = QPushButton("安装缺失工具（多源回退）")
        self.btn_tools.clicked.connect(self._tools_install)
        row.addWidget(self.btn_tools)
        self.btn_ext = QPushButton("安装全部 DCC 扩展")
        self.btn_ext.setToolTip(
            "把 USF 需要的宿主扩展（如 Premiere 抽帧面板）装入已安装的"
            " DCC 软件。本地复制无需联网, 秒级完成; 宿主未装的自动跳过。")
        self.btn_ext.clicked.connect(self._ext_install)
        row.addWidget(self.btn_ext)
        layout.addLayout(row)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlaceholderText(
            "pip / 工具安装输出在此滚动; 训练环境不完整时显示初始化命令。")
        layout.addWidget(self.console)

        bottom = QHBoxLayout()
        self.btn_train = QPushButton("一键创建训练环境（自动下载安装）")
        self.btn_train.setToolTip(
            "全自动完成: 创建 .venv → 安装 torch+CUDA（cu128, 官方源失败自动回退"
            "交大镜像）→ 编译安装 3 个 CUDA 子模块 → 验证 GPU 可用。\n"
            "需系统 Python 3.12；子模块编译需 VS Build Tools(C++) 与 CUDA Toolkit。")
        self.btn_train.clicked.connect(self._train_install)
        bottom.addWidget(self.btn_train)
        self.btn_copy_cmd = QPushButton("复制手动命令")
        self.btn_copy_cmd.clicked.connect(self._copy_train_cmd)
        bottom.addWidget(self.btn_copy_cmd)
        bottom.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

        self._train_cmd = ""
        self._report: list = []
        self.refresh()

    # ------------------------------------------------------------------ #
    def refresh(self):
        self._report = deps.full_report(self.settings) \
            + deps.check_training_env(self.settings) \
            + extension_manager.check_extensions(self.settings)
        self.table.setRowCount(len(self._report))
        for i, d in enumerate(self._report):
            self._fill_row(i, d)
        self._update_verdict()
        self._start_torch_probe()
        if any(d.kind == "train" and not d.ok for d in self._report):
            self._show_train_cmd()

    def _fill_row(self, row: int, d):
        self.table.setItem(row, 0, QTableWidgetItem(d.name))
        kind_label = DEP_KIND_LABELS.get(d.kind, d.kind)
        self.table.setItem(row, 1, QTableWidgetItem(kind_label))
        if d.ok:
            status, color = "✔ " + d.detail, Qt.green
        elif d.kind == "ext":
            # 扩展为可选集成（宿主装了才需要）: 用琥珀色提示而非红色告警
            status, color = "◐ " + d.detail, Qt.darkYellow
        else:
            status, color = "✘ " + d.detail, Qt.red
        item = QTableWidgetItem(status)
        item.setForeground(color)
        self.table.setItem(row, 2, item)
        self.table.setItem(row, 3, QTableWidgetItem(d.fix_hint if not d.ok else ""))

    def _update_verdict(self):
        """顶部结论行: 直接回答「这些依赖够不够执行这个软件」。"""
        rep = self._report
        if not rep:
            return
        core_miss = [d for d in rep
                     if d.kind in ("python", "tool") and not d.ok]
        train_miss = [d for d in rep if d.kind in ("train", "cuda") and not d.ok]
        if not core_miss and not train_miss:
            self.lbl_verdict.setText(
                '<span style="color:#22c55e;font-weight:bold;">✔ 依赖完整</span>'
                " — GUI 与全部 8 个重建阶段均可执行（含 3DGS 训练）。")
        elif not core_miss:
            self.lbl_verdict.setText(
                '<span style="color:#f59e0b;font-weight:bold;">◐ 基础流水线可运行</span>'
                f" — 采集 / SfM / 网格 / 导出正常; 训练链缺 {len(train_miss)} 项"
                "（红色行）, 3DGS 训练阶段将失败或退化, 按修复建议补齐即可。")
        else:
            self.lbl_verdict.setText(
                f'<span style="color:#ef4444;font-weight:bold;">✘ {len(core_miss)} 项核心依赖缺失</span>'
                "（红色行）— 安装后才可执行完整流水线; 各阶段失败会自动跳过不阻断。")

    def _start_torch_probe(self):
        old = getattr(self, "_torch_thread", None)
        if old is not None:
            try:
                old.result.disconnect(self._on_torch)
            except (RuntimeError, TypeError):
                pass
        self._torch_thread = _TorchProbeThread(self.settings)
        self._torch_thread.result.connect(self._on_torch)
        self._torch_thread.start()

    def _on_torch(self, d):
        self._report.append(d)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._fill_row(row, d)
        self._update_verdict()
        if not d.ok and not self.console.toPlainText().strip():
            self._show_train_cmd()

    def _show_train_cmd(self):
        self._train_cmd = deps.training_setup_text(self.settings)
        self.console.setPlainText(
            "── 训练环境未就绪: 点击下方「一键创建训练环境」自动安装 ──\n"
            "（也可点「复制手动命令」在 CMD 中逐行执行）\n"
            + self._train_cmd)

    # ------------------------------------------------------------------ #
    def _save_mirror(self, index: int):
        key = self.mirror.itemData(index)
        if key and key != self.settings.pip_mirror:
            self.settings.pip_mirror = key
            self.settings.save()

    def _pip_install(self):
        missing_pkgs = [d.package for d in deps.missing(deps.check_python())]
        if not missing_pkgs:
            QMessageBox.information(self, "完成", "Python 依赖完整")
            return
        if deps.is_frozen() and deps.pip_interpreter() is None:
            QMessageBox.warning(
                self, "缺少系统 Python",
                "打包版一键安装需要系统装有与主程序同版的 Python"
                "（推荐 3.12 x64, 含 py 启动器）用于下载 wheel 包。\n"
                "请从 python.org 安装后重试, 或在任意 Python 环境手动执行:\n"
                f"pip install {' '.join(missing_pkgs)}")
            return
        self.btn_pip.setEnabled(False)
        self._thread = _PipThread(missing_pkgs, self.mirror.currentData())
        self._thread.log_line.connect(self.console.appendPlainText)
        self._thread.finished.connect(self._on_pip_done)
        self._thread.start()

    def _on_pip_done(self):
        self.btn_pip.setEnabled(True)
        code = getattr(getattr(self, "_thread", None), "exit_code", 0)
        if code != 0:
            self.console.appendPlainText("✘ pip 安装失败（退出码 "
                                         f"{code}）, 详情见上方输出")
            if self.mirror.currentData() == "none":
                self.console.appendPlainText(
                    "⚠ 当前镜像为官方直连; 国内网络常超时, "
                    "建议在上方镜像下拉切换为清华/阿里等国内源后重试")
        self.refresh()

    # ------------------------------------------------------------------ #
    def _tools_install(self):
        keys = [d.name for d in deps.missing(deps.check_tools(self.settings))
                if d.name in OFFICIAL_SOURCES]
        if not keys:
            QMessageBox.information(self, "完成", "系统工具已就绪（FFmpeg / COLMAP）")
            return
        ans = QMessageBox.question(
            self, "确认安装",
            "即将从官方渠道下载便携版工具: <b>" + ", ".join(keys) + "</b><br>"
            f"安装位置: {self.settings.tools_root}<br><br>"
            "下载支持多镜像自动回退（Blender 3 源 / FFmpeg 2 源）, "
            "单个源失败自动切换下一个。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            return
        self.btn_tools.setEnabled(False)
        self._tool_thread = _ToolInstallThread(self.settings, keys)
        self._tool_thread.log_line.connect(self.console.appendPlainText)
        self._tool_thread.finished.connect(self._on_tools_done)
        self._tool_thread.start()

    def _on_tools_done(self):
        self.btn_tools.setEnabled(True)
        self.refresh()

    # ------------------------------------------------------------------ #
    def _ext_install(self):
        """一键安装全部 DCC 扩展（本地复制, 秒级完成, 无需下载线程）。"""
        exts = extension_manager.installable(self.settings)
        if not exts:
            QMessageBox.information(
                self, "完成",
                "DCC 扩展均已就绪（或对应宿主软件未安装, 无需扩展）。\n"
                "安装新 DCC 软件后重新打开本对话框即可自动检测。")
            return
        names = "\n".join(f"  · {e.label} → {e.host_label}" for e in exts)
        ans = QMessageBox.question(
            self, "确认安装扩展",
            "即将把以下扩展装入对应宿主软件（本地复制, 无需联网）:\n"
            + names + "\n\n是否继续?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            return
        self.btn_ext.setEnabled(False)
        results = extension_manager.install_all(
            self.settings, self.console.appendPlainText)
        self.btn_ext.setEnabled(True)
        if all(ok for _l, ok, _d in results):
            QMessageBox.information(
                self, "完成", "全部 DCC 扩展安装成功, 可直接使用对应功能。")
        self.refresh()

    # ------------------------------------------------------------------ #
    def _train_install(self):
        """一键创建训练环境: venv → torch+CUDA(多源回退) → 子模块 → 验证。"""
        repo = Path(self.settings.gs_repo)
        if not (repo / "train.py").exists():
            QMessageBox.warning(
                self, "缺少 gaussian-splatting 仓库",
                f"未在以下位置找到 train.py:\n{repo}\n\n"
                "仓库随安装包分发, 请重新安装本软件后重试。")
            return
        if deps.training_python(self.settings) is None \
                and deps.venv_interpreter() is None:
            QMessageBox.warning(
                self, "缺少系统 Python",
                "自动创建训练环境需要系统装有 Python（推荐 3.12 x64）。\n"
                "请从 python.org 安装（勾选 py 启动器）后重试。")
            return
        ans = QMessageBox.question(
            self, "确认一键创建训练环境",
            "将自动执行（全程约 10~40 分钟, 取决于网速）:\n\n"
            "  1. 创建独立虚拟环境 .venv（不影响系统 Python）\n"
            "  2. 下载安装 torch+CUDA（cu128, 约 3 GB; 官方源失败自动回退交大镜像）\n"
            "  3. 编译安装 3 个 CUDA 子模块（需 VS Build Tools 与 CUDA Toolkit）\n"
            "  4. 验证 torch 能否调用 GPU\n\n"
            "已有组件会自动跳过/复用（本按钮也可用于修复损坏的环境）。\n是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            return
        self.btn_train.setEnabled(False)
        self.btn_copy_cmd.setEnabled(False)
        self.console.clear()
        self._train_thread = _TrainSetupThread(self.settings)
        self._train_thread.log_line.connect(self.console.appendPlainText)
        self._train_thread.finished.connect(self._on_train_done)
        self._train_thread.start()

    def _on_train_done(self):
        self.btn_train.setEnabled(True)
        self.btn_copy_cmd.setEnabled(True)
        self.refresh()

    # ------------------------------------------------------------------ #
    def _copy_train_cmd(self):
        if not self._train_cmd:
            self._train_cmd = deps.training_setup_text(self.settings)
        QApplication.clipboard().setText(self._train_cmd)
        QMessageBox.information(
            self, "已复制",
            "训练环境初始化命令已复制到剪贴板。\n"
            "打开 CMD 粘贴执行: 创建 venv → 安装 torch(cu128) → 编译三个 CUDA 子模块。\n"
            "（推荐直接使用「一键创建训练环境」按钮, 无需手动执行）")


class _PipThread(QThread):
    log_line = Signal(str)

    def __init__(self, packages, mirror_key):
        super().__init__()
        self.packages = packages
        self.mirror_key = mirror_key
        self.exit_code = 1          # 挂前默认失败, 防 wait 前被读
        _bg_threads.add(self)
        self.finished.connect(lambda: _bg_threads.discard(self))

    def run(self):
        proc = deps.pip_install(self.packages, self.mirror_key)
        for line in proc.stdout or []:
            self.log_line.emit(line.rstrip())
        code = proc.wait()
        self.exit_code = code
        self.log_line.emit(f"pip 退出码: {code}")


class _TorchProbeThread(QThread):
    """训练环境 torch+CUDA 后台探测（子进程导入 5–60s, 不阻塞 GUI）。"""
    result = Signal(object)

    def __init__(self, settings: PipelineSettings):
        super().__init__()
        self.settings = settings
        _bg_threads.add(self)
        self.finished.connect(lambda: _bg_threads.discard(self))

    def run(self):
        self.result.emit(deps.check_training_torch(self.settings))


class _ToolInstallThread(QThread):
    """工具便携版多源下载线程; 成功后回写 settings 供流水线直接调用。"""
    log_line = Signal(str)

    def __init__(self, settings: PipelineSettings, keys: list):
        super().__init__()
        self.settings = settings
        self.keys = list(keys)
        self._last_mb = -1
        _bg_threads.add(self)
        self.finished.connect(lambda: _bg_threads.discard(self))

    def run(self):
        installer = AutoInstaller(
            tools_root=Path(self.settings.tools_root),
            progress_cb=self._on_progress,
            allow_silent=True)         # 用户已在对话框内确认
        installed = {}
        for key in self.keys:
            self.log_line.emit(f"── 开始安装 {key}（多源回退下载）──")
            exe = installer.ensure(key)
            if exe:
                installed[key] = exe
                self.log_line.emit(f"✔ {key} 安装完成: {exe}")
            else:
                self.log_line.emit(f"✘ {key} 安装失败, 详见日志")
        if "colmap" in installed:
            self.settings.colmap_exe = str(installed["colmap"])
        if "ffmpeg" in installed:
            self.settings.ffmpeg_exe = str(installed["ffmpeg"])
        if installed:
            try:
                self.settings.save()
                self.log_line.emit("已保存到 settings（流水线将直接调用以上路径）")
            except OSError as exc:
                self.log_line.emit(
                    f"⚠ settings 保存失败（本次会话仍生效, 重启后自动从"
                    f"工具目录找回）: {exc}")

    def _on_progress(self, done: int, total: int):
        if not total:
            return
        mb = done >> 20
        if mb >= self._last_mb + 8 or done >= total:
            self._last_mb = mb
            self.log_line.emit(f"  下载中 {mb} / {total >> 20} MB")


class _TrainSetupThread(QThread):
    """训练环境一键安装线程: venv → torch(多源回退) → CUDA 子模块 → 验证。

    任何一步失败即中止并在控制台给出修复指引; 成功后回写 gs_python。
    """
    log_line = Signal(str)

    def __init__(self, settings: PipelineSettings):
        super().__init__()
        self.settings = settings
        self.ok = False
        _bg_threads.add(self)
        self.finished.connect(lambda: _bg_threads.discard(self))

    def run(self):
        emit = self.log_line.emit
        repo = Path(self.settings.gs_repo)
        emit("══ 一键创建训练环境开始 ══")

        for d in (deps.check_msvc(), deps.check_cuda_toolkit()):
            emit(("✔" if d.ok else "⚠") + f" 预检 {d.name}: {d.detail}")
            if not d.ok:
                emit(f"    {d.fix_hint}")

        emit("── 检查现有环境（约 5~60 秒）──")
        current = deps.check_training_torch(self.settings)
        if current.ok:
            emit(f"✔ 训练环境已就绪, 无需安装: {current.detail}")
            emit("（如需强制重建: 删除 external\\gaussian-splatting\\.venv 后再点本按钮）")
            self.ok = True
            return
        emit(f"○ 现状: {current.detail}")

        py = deps.ensure_venv_for_training(repo)
        if py is None:
            emit("✘ 创建 venv 失败: 需要系统 Python（推荐 3.12 x64）")
            return
        emit(f"✔ venv 就绪: {py}")

        torch_ok = False
        for label, cmd in deps.torch_install_steps(self.settings):
            emit(f"── {label} ──")
            emit("$ " + subprocess.list2cmdline(cmd))
            if deps.run_streamed(cmd, emit) == 0:
                torch_ok = True
                break
            emit(f"✘ {label} 失败, 尝试回退…")
        if not torch_ok:
            emit("✘ torch 安装失败: 官方源与交大镜像均不可用, "
                 "请检查网络后重试（已装部分不影响重试, pip 会断点续装）")
            return

        cmd = deps.submodules_install_cmd(self.settings)
        emit("── 编译安装 3 个 CUDA 子模块（本机编译, 约 5~20 分钟）──")
        emit("$ " + subprocess.list2cmdline(cmd))
        # VSLANG=1033: GBK 代码页机器上 cl.exe 输出解码崩溃的已知规避
        if deps.run_streamed(cmd, emit, env={"VSLANG": "1033"}) != 0:
            emit("✘ 子模块编译失败: 请确认已安装 VS Build Tools"
                 "（C++ 生成工具）与 CUDA Toolkit 后重试本按钮")
            return

        emit("── 验证 torch+CUDA ──")
        d = deps.check_training_torch(self.settings)
        emit(("✔" if d.ok else "✘") + f" {d.name}: {d.detail}")
        self.ok = d.ok
        if self.ok:
            self.settings.gs_python = str(py)
            self.settings.save()
            emit("✔ 训练环境创建完成, 已保存到 settings（可直接开始重建）")
        else:
            emit(f"⚠ {d.fix_hint}")


class _MultiDirDialog(QDialog):
    """多选图像目录对话框。

    Qt 原生 getExistingDirectory 仅支持单选; 本对话框用 QTreeView + 文件系统模型
    实现一次勾选多个目录, 与文件多选/批量拖放共同构成「同时添加」能力。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择图像目录（可多选）")
        self.resize(660, 540)
        lay = QVBoxLayout(self)

        hint = QLabel("按住 Ctrl / Shift 可一次选中多个目录；双击展开进入子目录。")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.NoDotAndDotDot | QDir.Dirs | QDir.Drives)
        self.view = QTreeView(self)
        self.view.setModel(self.model)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setExpandsOnDoubleClick(True)
        for col in range(1, 4):          # 只留名称列
            self.view.hideColumn(col)
        self.view.expand(self.model.setRootPath(QDir.homePath()))
        lay.addWidget(self.view, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("添加所选目录")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        lay.addLayout(btns)

    def selected_dirs(self) -> list:
        sel = self.view.selectionModel()
        if sel is None:
            return []
        return [self.model.filePath(idx) for idx in sel.selectedIndexes()
                if idx.column() == 0]


class PathsDialog(QDialog):
    """项目调用路径设置: 查看自动探测结果, 手动覆盖任意 DCC 的调用入口。

    - 「自动探测结果」列展示三级探测（环境变量→注册表→常见路径/Steam）命中的 exe
    - 「手动指定路径」留空 = 自动探测; 填写后优先级最高
    - 保存后主窗口自动重新探测并刷新指示灯
    """

    def __init__(self, settings: PipelineSettings, dcc_infos: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.dcc_infos = dcc_infos
        self.setWindowTitle("项目调用路径设置")
        self.resize(920, 660)
        layout = QVBoxLayout(self)

        hint = QLabel("留空 = 三级自动探测（环境变量 → 注册表 → 常见安装路径/Steam 库）；"
                      "手动指定的路径优先级最高，用于安装位置非标准的软件。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(len(DCC_PATH_FIELDS), 3)
        self.table.setHorizontalHeaderLabels(
            ["软件", "自动探测结果", "手动指定调用路径（留空 = 自动）"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._edits: dict[str, QLineEdit] = {}
        for i, (key, name, field) in enumerate(DCC_PATH_FIELDS):
            self.table.setRowHeight(i, 44)
            self.table.setItem(i, 0, QTableWidgetItem(name))
            info = self.dcc_infos.get(key)
            detected = (info.exe or "") if info and info.available else "未检测到"
            self.table.setItem(i, 1, QTableWidgetItem(detected))

            cell = QWidget()
            row = QHBoxLayout(cell)
            row.setContentsMargins(4, 4, 4, 4)
            edit = QLineEdit(getattr(self.settings, field) or "")
            edit.setPlaceholderText("自动")
            self._edits[key] = edit
            btn = QPushButton("浏览…")
            btn.setFixedWidth(64)
            btn.clicked.connect(lambda _=False, k=key: self._browse(k))
            btn_clear = QPushButton("清除")
            btn_clear.setFixedWidth(52)
            btn_clear.clicked.connect(lambda _=False, k=key: self._edits[k].clear())
            row.addWidget(edit, 1)
            row.addWidget(btn)
            row.addWidget(btn_clear)
            self.table.setCellWidget(i, 2, cell)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(1, 300)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_reset = QPushButton("全部恢复自动")
        self.btn_reset.clicked.connect(self._reset_all)
        btn_row.addWidget(self.btn_reset)
        self.btn_save = QPushButton("保存并重新探测")
        self.btn_save.setDefault(True)
        self.btn_save.clicked.connect(self._save)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------ #
    def _browse(self, key: str):
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择可执行文件", "",
            "可执行文件 (*.exe);;所有文件 (*.*)")
        if path:
            self._edits[key].setText(path.replace("/", "\\"))

    def _reset_all(self):
        for edit in self._edits.values():
            edit.clear()

    def _save(self):
        for key, _name, field in DCC_PATH_FIELDS:
            setattr(self.settings, field, self._edits[key].text().strip() or None)
        self.settings.save()
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = PipelineSettings.load()
        self.bus = EventBus()
        self.worker: PipelineThread | None = None
        self.leds: dict[str, DccLed] = {}
        self._detect_dcc()
        self._build_ui()
        self._wire_bus()

    # ------------------------------------------------------------------ #
    def _detect_dcc(self):
        overrides = {
            key: getattr(self.settings, field)
            for key, _name, field in DCC_PATH_FIELDS
        }
        self.dcc_infos = DccDetector(overrides).detect_all()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.resize(1280, 820)
        # 弹性布局下限(P6): 固定 1280x820 在小屏/高 DPI 下文本截断,
        # 允许缩放到 980x640 仍保持全部控件可用。
        self.setMinimumSize(980, 640)
        self._build_menubar()
        root = QVBoxLayout()
        splitter = QSplitter(Qt.Horizontal)

        # ---- 左: 输入源面板 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        box_in = QGroupBox("输入源（支持多选，视频 / 图片混合）")
        form = QFormLayout(box_in)
        self.list_sources = QListWidget()
        self.list_sources.setSelectionMode(
            QAbstractItemView.ExtendedSelection)
        self.list_sources.setAlternatingRowColors(True)
        self.list_sources.setMinimumHeight(140)
        self.list_sources.setToolTip(
            "可同时添加任意多个视频文件、图像目录或单张图片（混合不限）。\n"
            "「添加文件…」对话框中按 Ctrl / Shift 可混合多选视频+图片；\n"
            "「添加目录…」对话框中按 Ctrl / Shift 可一次选中多个目录；\n"
            "也可直接从资源管理器批量拖入（文件与目录均可）。")
        form.addRow(self.list_sources)
        src_row = QHBoxLayout()
        btn_add_files = QPushButton("添加文件…")
        btn_add_files.clicked.connect(self._add_source_files)
        btn_add_files.setToolTip("支持视频与图片混合多选, 格式不限数量")
        btn_add_dir = QPushButton("添加目录…")
        btn_add_dir.clicked.connect(self._add_source_dir)
        btn_add_dir.setToolTip("对话框中按 Ctrl / Shift 可一次选中多个图像目录")
        btn_remove = QPushButton("移除所选")
        btn_remove.clicked.connect(self._remove_selected_sources)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self.list_sources.clear)
        for b in (btn_add_files, btn_add_dir, btn_remove, btn_clear):
            src_row.addWidget(b)
        wrap_src = QWidget()
        wrap_src.setLayout(src_row)
        form.addRow(wrap_src)
        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.IconMode)
        self.thumbs.setIconSize(self.thumbs.sizeHint() * 3)
        self.thumbs.setResizeMode(QListWidget.Adjust)
        self.thumbs.setMinimumHeight(160)
        form.addRow("输入预览:", self.thumbs)
        lv.addWidget(box_in)

        box_par = QGroupBox("重建参数")
        pform = QFormLayout(box_par)
        self.sp_fps = QDoubleSpinBox()
        self.sp_fps.setDecimals(1)
        self.sp_fps.setRange(0.1, 999.0)
        self.sp_fps.setValue(self.settings.frame_fps)
        self.sp_fps.setToolTip("每秒从视频抽取的帧数, 不设上限。\n"
                               "填源视频帧率即全帧率抽取。")
        pform.addRow("抽帧帧率 (fps):", self.sp_fps)
        self.sp_frames = QSpinBox()
        self.sp_frames.setRange(0, 999_999_999)
        self.sp_frames.setGroupSeparatorShown(True)
        self.sp_frames.setValue(self.settings.max_frames)
        self.sp_frames.setToolTip(
            "每个视频最多抽取的帧数, 不设上限。\n"
            "填 0 = 不限制, 抽取全部帧。")
        pform.addRow("抽帧上限 (0=不限):", self.sp_frames)
        self.sp_iters = QSpinBox()
        self.sp_iters.setRange(1, 999_999_999)
        self.sp_iters.setSingleStep(1000)
        self.sp_iters.setGroupSeparatorShown(True)
        self.sp_iters.setValue(self.settings.gs_iterations)
        self.sp_iters.setToolTip("3DGS 训练迭代次数, 不设上限。\n"
                                 "7000 快速预览, 30000 常规, 更高用于最终交付。")
        pform.addRow("3DGS 迭代数:", self.sp_iters)
        fmt_row = QHBoxLayout()
        self.fmt_checks: dict[str, QCheckBox] = {}
        for fmt in ["fbx", "obj", "glb", "blend", "uasset"]:
            cb = QCheckBox(fmt)
            cb.setChecked(fmt in self.settings.export_formats)
            self.fmt_checks[fmt] = cb
            fmt_row.addWidget(cb)
        wrap = QWidget()
        wrap.setLayout(fmt_row)
        pform.addRow("导出格式:", wrap)
        self.cb_repair = QCheckBox("启用 Metashape 洞穴修复 (需 Pro 授权)")
        self.cb_repair.setChecked(self.settings.enable_metashape_repair)
        pform.addRow(self.cb_repair)
        lv.addWidget(box_par)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("▶ 开始重建")
        self.btn_start.setMinimumHeight(42)
        self.btn_start.clicked.connect(self._start)
        self.btn_cancel = QPushButton("■ 取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        btn_row.addWidget(self.btn_start, 2)
        btn_row.addWidget(self.btn_cancel, 1)
        lv.addLayout(btn_row)

        tools_row = QHBoxLayout()
        btn_deps = QPushButton("依赖体检")
        btn_deps.clicked.connect(lambda: DependencyDialog(self.settings, self).exec())
        btn_paths = QPushButton("调用路径设置")
        btn_paths.clicked.connect(self._open_paths_dialog)
        btn_manual = QPushButton("用户手册")
        btn_manual.clicked.connect(self._open_manual)
        tools_row.addWidget(btn_deps)
        tools_row.addWidget(btn_paths)
        tools_row.addWidget(btn_manual)
        lv.addLayout(tools_row)
        lv.addStretch(1)
        splitter.addWidget(left)

        # ---- 右: 进度 + DCC 灯 + 日志 ----
        right = QWidget()
        rv = QVBoxLayout(right)

        head = QHBoxLayout()
        self.lbl_stage = QLabel("就绪")
        self.lbl_stage.setStyleSheet("font-weight:bold; font-size:15px;")
        self.lbl_step = QLabel(f"阶段 0/{len(STAGE_ORDER)}")
        self.lbl_step.setStyleSheet("color:#9ca3af; font-size:12px;")
        head.addWidget(self.lbl_stage, 1)
        head.addWidget(self.lbl_step)
        rv.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.progress.setAlignment(Qt.AlignCenter)
        self.progress.setFixedHeight(22)
        self.progress.setStyleSheet(
            "QProgressBar { border:1px solid #4b5563; border-radius:6px;"
            " text-align:center; font-size:13px; font-weight:bold; }"
            " QProgressBar::chunk { background:#22c55e; border-radius:5px; }")
        rv.addWidget(self.progress)

        # 8 阶段步骤条: ✓完成 / ●进行中 / ○未到 / ✗失败
        steps_row = QHBoxLayout()
        steps_row.setSpacing(4)
        self.step_labels: list = []
        for short in STAGE_SHORT:
            lbl = QLabel(f"○{short}")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                STEP_STYLES["todo"][1]
                + " font-size:11px; border-radius:9px; padding:3px 2px;")
            self.step_labels.append(lbl)
            steps_row.addWidget(lbl, 1)
        wrap_steps = QWidget()
        wrap_steps.setLayout(steps_row)
        rv.addWidget(wrap_steps)
        self._stage_states = ["todo"] * len(STAGE_ORDER)

        self.lbl_msg = QLabel("添加输入源后点击「开始重建」")
        self.lbl_msg.setStyleSheet("color:#9ca3af; font-size:12px;")
        self.lbl_msg.setWordWrap(True)
        rv.addWidget(self.lbl_msg)

        box_dcc = QGroupBox("DCC 调用状态")
        grid = QGridLayout(box_dcc)
        led_items = [(key, name) for key, name, _f in DCC_PATH_FIELDS]
        led_items.append(("cuda", "GPU/CUDA"))
        for i, (key, name) in enumerate(led_items):
            led = DccLed(name)
            self.leds[key] = led
            grid.addWidget(led, i // 4, i % 4)
        rv.addWidget(box_dcc)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(
            "font-family:Consolas,'Courier New',monospace; font-size:12px;")
        rv.addWidget(self.console, 1)
        splitter.addWidget(right)
        splitter.setSizes([430, 850])
        root.addWidget(splitter)
        central = QWidget()
        central.setLayout(root)
        self.setCentralWidget(central)

        self.statusBar().showMessage(
            "添加/拖入多个视频与图片（可混合）, 点击「开始重建」")
        self.setAcceptDrops(True)
        self._refresh_leds_initial()

    # ------------------------------------------------------------------ #
    def _build_menubar(self):
        """顶部菜单栏: 文件 / 设置 / 帮助。"""
        menubar = self.menuBar()

        m_file = menubar.addMenu("文件(&F)")
        act_add_files = QAction("添加视频/图片输入…", self)
        act_add_files.setShortcut("Ctrl+O")
        act_add_files.triggered.connect(self._add_source_files)
        m_file.addAction(act_add_files)
        act_add_dir = QAction("添加图像目录…", self)
        act_add_dir.triggered.connect(self._add_source_dir)
        m_file.addAction(act_add_dir)
        act_pr = QAction("Premiere Pro 高效抽帧…", self)
        act_pr.triggered.connect(self._open_pr_dialog)
        m_file.addAction(act_pr)
        m_file.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_set = menubar.addMenu("设置(&S)")
        act_paths = QAction("软件调用路径…", self)
        act_paths.triggered.connect(self._open_paths_dialog)
        m_set.addAction(act_paths)
        act_deps = QAction("依赖体检与一键安装…", self)
        act_deps.triggered.connect(
            lambda: DependencyDialog(self.settings, self).exec())
        m_set.addAction(act_deps)

        m_help = menubar.addMenu("帮助(&H)")
        act_manual = QAction("用户手册", self)
        act_manual.setShortcut("F1")
        act_manual.triggered.connect(self._open_manual)
        m_help.addAction(act_manual)
        act_about = QAction("关于 Universal Scene Forge", self)
        act_about.triggered.connect(self._show_about)
        m_help.addAction(act_about)

    def _show_about(self):
        QMessageBox.about(
            self, "关于",
            f"<b>{APP_NAME}</b> v{__version__}<br>"
            "视频 / 图像多源输入 → SfM → 3DGS 训练 → 网格重建 → 多 DCC 导出<br>"
            f"支持 {len(DCC_PATH_FIELDS)} 款 DCC 软件自动探测与调用。")

    # ------------------------------------------------------------------ #
    def _wire_bus(self):
        self.bus.stage.connect(self._on_stage)
        self.bus.stage_failed.connect(self._on_stage_failed)
        self.bus.stage_skipped.connect(self._on_stage_skipped)
        self.bus.progress.connect(self._on_progress)
        self.bus.dcc.connect(self._on_dcc)
        self.bus.log_line.connect(self._on_log)
        self.bus.artifact.connect(lambda p: self.statusBar().showMessage(f"产物: {p}"))
        self.bus.preview.connect(self._on_preview)
        self.bus.finished.connect(self._on_finished)
        self._log_bridge = _QtLogBridge(self)
        self._log_bridge.attach(self._on_log)

    def _refresh_leds_initial(self):
        for key, _name, _f in DCC_PATH_FIELDS:
            info = self.dcc_infos.get(key)
            self.leds[key].set_state("ok" if info and info.available else "off")
        try:
            from app.utils import gpu
            info = gpu.probe()
            self.leds["cuda"].set_state("ok" if info.available else "off")
            if info.available:
                self.statusBar().showMessage(info.summary())
        except Exception:  # noqa: BLE001
            self.leds["cuda"].set_state("off")

    # ------------------------------------------------------------------ #
    #  事件
    # ------------------------------------------------------------------ #
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            self._add_source(url.toLocalFile())

    # ------------------------------------------------------------------ #
    #  多源输入管理
    # ------------------------------------------------------------------ #
    def _add_source(self, path: str):
        path = path.strip()
        if not path:
            return
        existing = {self.list_sources.item(i).text()
                    for i in range(self.list_sources.count())}
        if path in existing:
            return
        self.list_sources.addItem(path)

    def _add_source_files(self):
        from app.core.ingest import VIDEO_EXTS, IMAGE_EXTS
        exts = sorted(VIDEO_EXTS | IMAGE_EXTS)
        pattern = " ".join(f"*{e}" for e in exts)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择一个或多个视频 / 图片（可混合多选）", "",
            f"视频/图像 ({pattern});;所有文件 (*.*)")
        for p in paths:
            self._add_source(p)

    def _add_source_dir(self):
        dlg = _MultiDirDialog(self)
        if dlg.exec() == QDialog.Accepted:
            for p in dlg.selected_dirs():
                self._add_source(p)

    def _open_pr_dialog(self):
        """Premiere Pro 高效抽帧: 输入源中的视频带入对话框, 导出完成后
        帧目录自动回填为输入源（图片目录走既有 collect_multi 链路）。"""
        from app.views.pr_dialog import PrFrameDialog
        videos = [self.list_sources.item(i).text()
                  for i in range(self.list_sources.count())
                  if Path(self.list_sources.item(i).text())
                  .suffix.lower() in VIDEO_EXTS]
        dlg = PrFrameDialog(self.settings, videos, self)
        dlg.frames_ready.connect(self._add_source)
        dlg.exec()

    def _remove_selected_sources(self):
        for item in reversed(self.list_sources.selectedItems()):
            self.list_sources.takeItem(self.list_sources.row(item))

    def _sources(self) -> list[Path]:
        return [Path(self.list_sources.item(i).text()).resolve()
                for i in range(self.list_sources.count())
                if self.list_sources.item(i).text().strip()]

    # ------------------------------------------------------------------ #
    def _open_paths_dialog(self):
        dlg = PathsDialog(self.settings, self.dcc_infos, self)
        if dlg.exec():
            self._detect_dcc()          # 路径覆盖已保存 → 重新探测
            self._refresh_leds_initial()
            self._on_log("INFO", "调用路径设置已保存, DCC 重新探测完成")

    def _open_manual(self):
        from app.utils.paths import docs_dir
        manual = docs_dir() / "user_manual.html"
        if not manual.exists():
            QMessageBox.information(
                self, "提示", f"未找到用户手册: {manual}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(manual)))

    def _start(self):
        sources = self._sources()
        if not sources:
            QMessageBox.warning(self, "提示",
                                "请先添加至少一个输入源（视频 / 图片 / 图像目录）")
            return
        missing = [str(s) for s in sources if not s.exists()]
        if missing:
            QMessageBox.warning(
                self, "提示", "以下输入源不存在:\n" + "\n".join(missing))
            return
        # 参数写回 settings
        self.settings.frame_fps = self.sp_fps.value()
        self.settings.max_frames = self.sp_frames.value()
        self.settings.gs_iterations = self.sp_iters.value()
        self.settings.export_formats = [f for f, cb in self.fmt_checks.items() if cb.isChecked()]
        self.settings.enable_metashape_repair = self.cb_repair.isChecked()
        self.settings.save()

        # 工作目录跟随首个输入源: 文件 → 同级 usf_work, 目录 → 子级 usf_work
        first = sources[0]
        work_dir = first.parent / "usf_work" if first.is_file() \
            else first / "usf_work"
        controller = PipelineController(self.settings, self.bus,
                                        dcc_infos=self.dcc_infos)
        self.worker = PipelineThread(controller, sources, work_dir, self)
        self.worker.done.connect(self._on_worker_done)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.lbl_step.setText(f"阶段 0/{len(STAGE_ORDER)}")
        for i in range(len(self.step_labels)):
            self._set_step_state(i, "todo")
        self.lbl_msg.setText("正在启动流水线…")
        self.console.clear()
        self.worker.start()

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.controller.request_stop()
            self._on_log("WARNING", "已请求取消, 等待子进程终止…")

    def _on_worker_done(self, ok: bool, msg: str):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if msg:
            self._on_log("ERROR", msg)
            self._alert_failure("重建异常终止", msg)

    def _on_progress(self, value: int, msg: str):
        self.progress.setValue(value)
        if msg:
            self.lbl_msg.setText(msg)

    # ------------------------------------------------------------------ #
    #  阶段步骤条
    # ------------------------------------------------------------------ #
    def _set_step_state(self, idx: int, state: str):
        if not 0 <= idx < len(self.step_labels):
            return
        mark, style = STEP_STYLES[state]
        self.step_labels[idx].setText(f"{mark}{STAGE_SHORT[idx]}")
        self.step_labels[idx].setStyleSheet(
            style + " font-size:11px; border-radius:9px; padding:3px 2px;")
        self._stage_states[idx] = state

    def _on_stage(self, name: str):
        self.lbl_stage.setText(name)
        if name not in STAGE_ORDER:
            return
        idx = STAGE_ORDER.index(name)
        self.lbl_step.setText(f"阶段 {idx + 1}/{len(STAGE_ORDER)}")
        for i in range(len(self.step_labels)):
            if i == idx:
                self._set_step_state(i, "active")
            elif i > idx:
                self._set_step_state(i, "todo")
            elif self._stage_states[i] == "active":   # 上一个进行中 → 完成
                self._set_step_state(i, "done")

    def _on_stage_failed(self, name: str):
        if name in STAGE_ORDER:
            self._set_step_state(STAGE_ORDER.index(name), "failed")

    def _on_stage_skipped(self, name: str):
        """主动跳过的阶段(P4): 蓝色 ◇ 留档, 与绿色成功/红色失败区分。"""
        if name in STAGE_ORDER:
            self._set_step_state(STAGE_ORDER.index(name), "skipped")

    def _on_dcc(self, key: str, state: str):
        if key in self.leds:
            self.leds[key].set_state(state)

    def _on_log(self, level: str, message: str):
        color = {"ERROR": "#ef4444", "WARNING": "#f59e0b"}.get(level, "#d1d5db")
        self.console.appendHtml(
            f'<span style="color:{color};">[{level}]</span> '
            f'{message.replace("<", "&lt;")}')

    def _on_preview(self, thumbs_dir: str):
        self.thumbs.clear()
        for img in sorted(Path(thumbs_dir).glob("thumb_*.jpg"))[:12]:
            pix = QPixmap(str(img))
            item = QListWidgetItem()
            item.setIcon(pix if not pix.isNull() else QPixmap())
            self.thumbs.addItem(item)

    def _on_finished(self, ok: bool, summary: str):
        self.progress.setValue(100 if ok else self.progress.value())
        for i, st in enumerate(self._stage_states):   # 收尾: 进行中的标记为完成/失败
            if st == "active":
                self._set_step_state(i, "done" if ok else "failed")
        self.lbl_msg.setText(summary)
        self._on_log("INFO" if ok else "WARNING", f"=== {summary} ===")
        self.statusBar().showMessage(summary)
        if not ok:
            self._alert_failure("重建完成（含失败步骤）", summary)

    def _alert_failure(self, title: str, detail: str):
        """失败醒目弹窗（可被测试替换以避免模态阻塞）。"""
        QMessageBox.warning(
            self, title,
            f"{detail}<br><br>"
            "失败步骤已用 <b style='color:#ef4444;'>✗</b> 标记在上方步骤条, "
            "详细原因见下方日志控制台。<br>"
            "常见修复: <b>依赖体检</b> 中一键安装 COLMAP / FFmpeg 等缺失组件; "
            "训练失败可降低 3DGS 迭代数或减少输入帧数后重试。")


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec()
