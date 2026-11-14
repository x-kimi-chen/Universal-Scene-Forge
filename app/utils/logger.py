"""统一日志：控制台 + GUI + 文件三通道。

GUI 通过 install_qt_handler() 挂接 QtLogHandler，把日志行以信号形式
投递到主窗口控制台（跨线程自动走队列连接）。
文件通道: 每次运行写 logs/usf_YYYYMMDD_HHMMSS.log —— 冻结 exe 无控制台,
GUI 控制台一旦关闭现场即丢失, 落盘是事后诊断「开始重建失败」的唯一线索。
"""
from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path
from typing import Optional

_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"

# --------------------------------------------------------------------- #
#  日志脱敏（SECURITY_AUDIT.md L-01），要遮的就三类
#  API key / token / password / Authorization 头
#  带用户名的绝对路径（C:\Users\<name>、/home/<name> 这种）
#  还有 sk-、hf_、ghp_ 开头的密钥串
#  遮在 Handler 层做，控制台、界面、文件三个口子口径一致
# --------------------------------------------------------------------- #
_SECRET_KV = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?key|secret|token|password|passwd|"
    r"private[_-]?key)\b(\s*[=:]\s*|\s+)([\"']?)([^\s\"'&,;]+)")
_AUTH_HEADER = re.compile(
    r"(?i)\b(authorization\s*[=:]\s*(?:bearer|basic)\s+)\S+")
_KEY_PREFIX = re.compile(r"\b(sk-|hf_|ghp_|glpat-)[A-Za-z0-9_\-]{12,}")
_WIN_USER = re.compile(r"(?i)([A-Za-z]:\\Users\\)([^\\\s\"']+)")
_POSIX_HOME = re.compile(r"(?i)(/home/|/Users/)([^/\s\"']+)")


def redact(text: str) -> str:
    """返回脱敏后的日志文本（不改变非敏感内容）。"""
    if not text:
        return text
    out = _SECRET_KV.sub(lambda m: m.group(1) + m.group(2) + m.group(3) + "***",
                         text)
    out = _AUTH_HEADER.sub(lambda m: m.group(1) + "***", out)
    out = _KEY_PREFIX.sub(lambda m: m.group(1) + "***", out)
    out = _WIN_USER.sub(lambda m: m.group(1) + "<user>", out)
    out = _POSIX_HOME.sub(lambda m: m.group(1) + "<user>", out)
    return out


class RedactionFilter(logging.Filter):
    """在 Handler 上挂载: 把 record 的消息脱敏后再输出。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # 格式化挂了就原样输出，别把日志通道带崩
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True



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
        except Exception:  # 记日志这件事本身不能把主流程搞挂，这条优先级最高
            pass


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        # 把日志绑到 stdout（P5）。logging 默认写 stderr，PowerShell 一看就整行标红
        # 报 NativeCommandError，用户以为程序炸了。跟 print 同流，顺序也顺了
        # 窗口版 exe 没有控制台，sys.stdout 是 None，这时 StreamHandler(None)
        # 自己会退回 stderr，行为不变
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        console.addFilter(RedactionFilter())
        root.addHandler(console)
        try:   # 文件通道万一写不了（只读盘、没权限），也不能拖垮启动
            logfile = _log_file_path()
            # 用 utf-8-sig 带 BOM，记事本和 Excel 就不会按 ANSI 猜，中文不再乱码（BUG-008）
            fileh = logging.FileHandler(logfile, encoding="utf-8-sig")
            fileh.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
            fileh.addFilter(RedactionFilter())
            root.addHandler(fileh)
            root.info("日志文件: %s", logfile)
        except OSError:
            pass
    return logging.getLogger("USF")


def install_qt_handler(sink) -> Optional[QtLogHandler]:
    """挂接 GUI 日志通道；返回 handler 以便退出时移除。"""
    handler = QtLogHandler(sink)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    handler.addFilter(RedactionFilter())
    logging.getLogger().addHandler(handler)
    return handler


def get_logger(name: str = "USF") -> logging.Logger:
    return logging.getLogger(f"USF.{name}" if name != "USF" else "USF")
