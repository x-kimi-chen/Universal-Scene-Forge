"""统一日志：控制台 + GUI + 文件三通道。

GUI 通过 install_qt_handler() 挂接 QtLogHandler，把日志行以信号形式
投递到主窗口控制台（跨线程自动走队列连接）。
文件通道: 每次运行写 logs/usf_YYYYMMDD_HHMMSS.log —— 冻结 exe 无控制台,
GUI 控制台一旦关闭现场即丢失, 落盘是事后诊断「开始重建失败」的唯一线索。
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Optional

_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"


def _log_file_path() -> Path:
    """日志落盘目录: 冻结模式 ~/.universal_scene_forge/logs（安装目录通常
    不可写）; 源码模式 <项目根>/logs。"""
    if getattr(sys, "frozen", False):
        base = Path.home() / ".universal_scene_forge" / "logs"
    else:
        base = Path(__file__).resolve().parent.parent.parent / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / time.strftime("usf_%Y%m%d_%H%M%S.log")


class QtLogHandler(logging.Handler):
    """把日志转发给任意 callable(level: str, message: str)。"""

    def __init__(self, sink) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink(record.levelname, self.format(record))
        except Exception:  # 日志通道自身绝不能反噬主流程
            pass


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        # 绑 stdout(P5): logging 默认走 stderr, PowerShell 会把整行渲染成
        # 红色 NativeCommandError, 观感等同报错。CLI 的 print/日志同流后
        # 也可正确排序。无控制台的 windowed exe 下 sys.stdout 为 None,
        # StreamHandler(None) 自动回退 sys.stderr, 行为不变。
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(console)
        try:   # 文件通道失败(只读盘/权限)不应拖垮启动
            logfile = _log_file_path()
            # utf-8-sig 写 BOM: 记事本/Excel 按 ANSI 猜编码时不再乱码(BUG-008)
            fileh = logging.FileHandler(logfile, encoding="utf-8-sig")
            fileh.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
            root.addHandler(fileh)
            root.info("日志文件: %s", logfile)
        except OSError:
            pass
    return logging.getLogger("USF")


def install_qt_handler(sink) -> Optional[QtLogHandler]:
    """挂接 GUI 日志通道；返回 handler 以便退出时移除。"""
    handler = QtLogHandler(sink)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    logging.getLogger().addHandler(handler)
    return handler


def get_logger(name: str = "USF") -> logging.Logger:
    return logging.getLogger(f"USF.{name}" if name != "USF" else "USF")
