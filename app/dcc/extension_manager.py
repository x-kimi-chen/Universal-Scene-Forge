"""DCC 宿主扩展的统一体检与一键安装。

USF 桥接的 16 款 DCC 大多通过命令行脚本调用（随包分发, 无需装入宿主）;
需要安装进宿主软件本体的扩展目前为:
- Premiere Pro CEP 面板 pr_frame_exporter（高效抽帧）

本模块把这类扩展收敛为统一清单 EXTENSIONS —— 依赖体检遍历展示状态,
「安装全部 DCC 扩展」遍历安装; 未来新增宿主扩展只需登记清单, UI 零改动。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List

from app.dcc import premiere_bridge
from app.models.settings import PipelineSettings
from app.utils.deps import DepStatus
from app.utils.logger import get_logger

log = get_logger("EXT")


@dataclass(frozen=True)
class DccExtensionSpec:
    key: str            # 唯一键
    label: str          # 依赖体检表格显示名
    host_label: str     # 宿主软件显示名
    host_present: Callable[[PipelineSettings], bool]
    installed: Callable[[PipelineSettings], bool]
    install: Callable[[], Path]


def _pr_host_present(settings: PipelineSettings) -> bool:
    return premiere_bridge.detect_premiere(settings.premiere_exe) is not None


def _pr_installed(settings: PipelineSettings) -> bool:
    return (premiere_bridge.extension_install_dir() / "CSXS" / "manifest.xml"
            ).is_file()


EXTENSIONS: List[DccExtensionSpec] = [
    DccExtensionSpec(
        key="pr_cep",
        label="Premiere 抽帧面板 (CEP)",
        host_label="Premiere Pro",
        host_present=_pr_host_present,
        installed=_pr_installed,
        install=lambda: premiere_bridge.install_extension()),
]


def check_extensions(settings: PipelineSettings) -> List[DepStatus]:
    """扩展体检（kind=ext）: 宿主未装 → 无需处理; 已装 → 就绪; 缺 → 待安装。"""
    out: List[DepStatus] = []
    for e in EXTENSIONS:
        if not e.host_present(settings):
            out.append(DepStatus(
                name=e.label, kind="ext", ok=True,
                detail=f"宿主 {e.host_label} 未安装, 暂不需要"))
        elif e.installed(settings):
            out.append(DepStatus(
                name=e.label, kind="ext", ok=True,
                detail=f"已装入 {e.host_label}"))
        else:
            out.append(DepStatus(
                name=e.label, kind="ext", ok=False, detail="未安装",
                fix_hint="点「安装全部 DCC 扩展」装入宿主软件"
                         "（本地复制, 无需联网）"))
    return out


def installable(settings: PipelineSettings) -> List[DccExtensionSpec]:
    """宿主已装但扩展未装的清单（一键安装的目标）。"""
    return [e for e in EXTENSIONS
            if e.host_present(settings) and not e.installed(settings)]


def install_all(settings: PipelineSettings,
                emit: Callable[[str], None]) -> List[tuple]:
    """安装全部可装扩展, emit 逐行回显; 返回 [(label, ok, dst_or_err)]。

    单个扩展失败不阻断其余（与流水线容错同哲学）。已装扩展不重装——
    PR 面板每次下发任务时幂等覆盖更新, 避免宿主运行中文件被占用。
    """
    results: List[tuple] = []
    for e in installable(settings):
        try:
            dst = e.install()
            emit(f"✔ {e.label} 已安装 → {dst}")
            log.info("扩展安装完成: %s → %s", e.label, dst)
            results.append((e.label, True, str(dst)))
        except Exception as exc:  # noqa: BLE001
            emit(f"✘ {e.label} 安装失败: {exc}")
            log.error("扩展安装失败: %s: %s", e.label, exc)
            results.append((e.label, False, str(exc)))
    return results
