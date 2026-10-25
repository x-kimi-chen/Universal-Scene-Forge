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
from app.utils.logger import get_logger, redact, setup_logging

log = get_logger("CLI")


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
    parser.add_argument("--model", type=Path,
                        help="外部 3DGS 点云 PLY（跳过 SFM/训练, 不执行 Inria 组件）")
    parser.add_argument("--accept-3dgs-license", action="store_true",
                        help="确认 gaussian-splatting 仅用于非商业用途（Inria/MPII）")
    parser.add_argument("--commercial-use", action="store_true",
                        help="声明商业用途；将拒绝执行 3DGS（需 Inria 商业授权）")
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


def _run_cli(sources: list, output: Path, model: Path = None,
             accept_3dgs: bool = False, commercial: bool = False) -> int:
    """CLI 模式。退出码语义(C-04): 0=全部成功, 1=任意阶段失败,
    3=输入源/环境阻断(未进入流水线)。参数错误由 argparse 侧返回 2。"""
    # C-03/M-01: 输入/输出统一规范化 + 输出根边界校验, 失败即环境阻断(3)
    from app.utils.security import (PathSecurityError, ensure_output_dir,
                                    normalize_input)
    try:
        sources = [normalize_input(s) for s in sources]
        output = ensure_output_dir(output)
        model_path = normalize_input(model) if model else None
        if model_path and not model_path.is_file():
            raise PathSecurityError(f"--model 不是文件: {model_path}")
    except PathSecurityError as exc:
        print(redact(f"输入/输出路径被拒绝: {exc}"))
        log.error("[SECURITY] CLI 路径预检失败: %s", exc)
        return 3

    from PySide6.QtCore import QCoreApplication  # EventBus 依赖 Qt 事件循环
    from app.controllers.pipeline_controller import EventBus, PipelineController
    from app.models.settings import PipelineSettings

    app = QCoreApplication([])
    bus = EventBus()
    # M-01: 文件日志经 RedactionFilter 脱敏, 控制台 print 是另一条通道, 必须
    # 同样调用 redact(); 否则子进程 stderr 尾部等消息会原样带出用户名路径。
    bus.log_line.connect(lambda lvl, msg: print(f"[{lvl}] {redact(msg)}"))
    bus.progress.connect(lambda p, m: print(redact(f"  {p:3d}% {m}"), flush=True))
    bus.finished.connect(lambda ok, msg: print(redact(f"\n=== {msg} ===")))

    settings = PipelineSettings.load()
    # 3.1: CLI 的许可证选择必须落盘并写审计日志（不可静默放行）
    if commercial:
        settings.commercial_use = True
        log.info("[LICENSE] CLI 声明商业用途: 3DGS 将被拒绝执行")
    if accept_3dgs:
        settings.license_ack_3dgs = True
        log.info("[LICENSE] CLI 确认 gaussian-splatting 仅用于非商业用途 "
                 "(Inria/MPII)")
    if commercial or accept_3dgs:
        try:
            settings.save()
        except OSError as exc:
            log.warning("[LICENSE] CLI 许可证选择写回 settings 失败: %s", exc)
    controller = PipelineController(settings, bus)
    result = {"ok": False}

    def _done(ok, _msg):
        result["ok"] = ok
        app.quit()

    bus.finished.connect(_done)
    from PySide6.QtCore import QTimer

    def _start():
        try:
            controller.execute(sources, output, model=model_path)
        except Exception as exc:  # noqa: BLE001 —— 预检外的异常不能挂起事件循环
            log.error("[X] 流水线启动失败: %s", exc)
            print(redact(f"流水线启动失败: {exc}"))
            result["ok"] = False
            app.quit()

    QTimer.singleShot(0, _start)
    app.exec()
    return 0 if result["ok"] else 1


def _preload_open3d() -> None:
    """主线程预载 open3d: 冻结环境下流水线线程内首次导入可能因 DLL 初始化
    失败(实测), 预载进 sys.modules 后流水线线程直接命中缓存。失败不阻断。"""
    try:
        import open3d  # noqa: F401
    except Exception as exc:  # noqa: BLE001 —— 缺失/异常交由依赖体检与各阶段兜底
        # 记录根因: 冻结环境常见 DLL 初始化失败, 只写"未安装"会掩盖真实原因。
        log.warning("open3d 预载失败: %s", exc)


def main() -> int:
    """进程入口：解析参数后分派到 GUI、命令行或依赖体检三条路径，返回退出码。"""
    setup_logging()
    from app.utils.paths import register_external_pkgs
    register_external_pkgs()   # 冻结壳 python_pkgs 补装目录（依赖体检一键安装的落地处）
    _preload_open3d()
    _reconfigure_stdio()
    args = _parse_args()
    if args.check_deps or args.cli:
        # 打包版为窗口子系统程序, 没有控制台时 print 无处可写（旧版静默退出）。
        from app.utils.console import ensure_console, notify_output_saved
        fallback = ensure_console()
        try:
            if args.check_deps:
                return _run_check_deps()
            if not args.input:
                print("--cli 模式需要 --input")
                return 2
            return _run_cli(args.input, args.output, model=args.model,
                            accept_3dgs=args.accept_3dgs_license,
                            commercial=args.commercial_use)
        finally:
            if fallback is not None:
                notify_output_saved(fallback)
    from app.views.main_window import run_gui
    return run_gui()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Ctrl+C 属于用户主动中止: 返回 130, 与阶段失败(1)区分, 便于脚本判断。
        print("\n已中断")
        sys.exit(130)
