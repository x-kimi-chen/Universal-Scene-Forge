"""Universal Scene Forge 入口。

用法:
    python main.py                     # GUI 模式
    python main.py --cli --input a.mp4 --output D:/work
    python main.py --check-deps        # 依赖体检报告
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import APP_NAME, __version__
from app.utils.logger import setup_logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="universal-scene-forge",
        description=f"{APP_NAME} — 3DGS 场景重建与 DCC 自动化导出")
    parser.add_argument("--version", action="version",
                        version=f"{APP_NAME} {__version__}")
    parser.add_argument("--cli", action="store_true", help="无界面命令行模式")
    parser.add_argument("--input", type=Path, nargs="+",
                        help="输入视频或图像目录（可同时指定多个）")
    parser.add_argument("--output", type=Path, default=Path("usf_work"),
                        help="工作/输出目录")
    parser.add_argument("--check-deps", action="store_true",
                        help="打印依赖体检报告后退出")
    return parser.parse_args()


def _reconfigure_stdio() -> None:
    """中文 Windows 控制台默认 GBK 代码页, print 含 Unicode 标记(✔/✘)会直接
    UnicodeEncodeError 崩溃(B-02)。改为 backslashreplace: 可编码字符原样输出,
    不可编码的降级为转义序列而非崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, OSError):
            pass


def _run_check_deps() -> int:
    from app.utils import gpu
    from app.utils import deps
    from app.models.settings import PipelineSettings
    report = deps.full_report(PipelineSettings.load())
    print(f"\n=== {APP_NAME} 依赖体检 ===")
    for d in report:
        mark = "[OK]" if d.ok else "[X]"     # ASCII 标记, GBK 控制台安全
        print(f"  {mark} {d.kind:<7} {d.name:<16} {d.detail}"
              + (f"  → {d.fix_hint}" if not d.ok else ""))
    info = gpu.probe()
    print(f"  [i] GPU: {info.summary()}")
    return 0 if all(d.ok for d in report) else 1


def _run_cli(sources: list, output: Path) -> int:
    """CLI 模式。退出码语义(C-04): 0=全部成功, 1=任意阶段失败,
    3=输入源/环境阻断(未进入流水线)。参数错误由 argparse 侧返回 2。"""
    missing = [str(s) for s in sources if not Path(s).exists()]
    if missing:
        print(f"输入源不存在: {', '.join(missing)}")
        return 3
    from PySide6.QtCore import QCoreApplication  # EventBus 依赖 Qt 事件循环
    from app.controllers.pipeline_controller import EventBus, PipelineController
    from app.models.settings import PipelineSettings

    app = QCoreApplication([])
    bus = EventBus()
    bus.log_line.connect(lambda lvl, msg: print(f"[{lvl}] {msg}"))
    bus.progress.connect(lambda p, m: print(f"  {p:3d}% {m}", flush=True))
    bus.finished.connect(lambda ok, msg: print(f"\n=== {msg} ==="))

    settings = PipelineSettings.load()
    controller = PipelineController(settings, bus)
    result = {"ok": False}

    def _done(ok, _msg):
        result["ok"] = ok
        app.quit()

    bus.finished.connect(_done)
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, lambda: controller.execute(sources, output))
    app.exec()
    return 0 if result["ok"] else 1


def main() -> int:
    setup_logging()
    from app.utils.paths import register_external_pkgs
    register_external_pkgs()   # 冻结壳 python_pkgs 补装目录（依赖体检一键安装的落地处）
    _reconfigure_stdio()
    args = _parse_args()
    if args.check_deps:
        return _run_check_deps()
    if args.cli:
        if not args.input:
            print("--cli 模式需要 --input")
            return 2
        return _run_cli(args.input, args.output)
    from app.views.main_window import run_gui
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
