# -*- coding: utf-8 -*-
"""许可证门（合规硬约束）。

对应 LICENSE_COMPLIANCE_CHANGES.md:
- 3.1 gaussian-splatting (Inria/MPII, 仅非商业): 商用声明即拒绝执行;
      CLI/GUI 必须显式确认, 确认结果写入日志; 组件只经 external/ 子进程调用。
- 3.2 FFmpeg (gyan.dev GPLv3): 自动下载前展示 GPLv3 义务并取得确认。
- 3.4 RizomUV / Metashape / UE5: 可选组件, 只检测用户自备授权, 检测不到
      即跳过并记录原因; 绝不模拟/绕过厂商许可证校验。
- 3.5 Blender (GPL) / PySide6 (LGPL): 维持子进程调用, 不 import bpy。

本模块只做"是否允许执行"的判定与审计日志, 不修改任何第三方代码。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from app.utils.logger import get_logger

log = get_logger("LICENSE")

ENV_COMMERCIAL = "USF_COMMERCIAL_USE"          # 1/true/yes = 用户声明商用
ENV_ACCEPT_3DGS = "USF_ACCEPT_3DGS_LICENSE"    # 1 = 已确认 Inria 非商业条款
ENV_LICENSE_DIR = "USF_LICENSE_DIR"            # 商业组件授权文件目录

INRIA_REPO = "https://github.com/graphdeco-inria/gaussian-splatting"
INRIA_LICENSE = ("https://github.com/graphdeco-inria/gaussian-splatting/"
                 "blob/main/LICENSE.md")
INRIA_COMMERCIAL = ("https://repo-sam.inria.fr/fungraph/3d-gaussian-splatting/"
                    "license.pdf")

# 可选商业组件: (显示名, 授权检测环境变量, 授权文件默认名, 获取途径)
OPTIONAL_COMPONENTS = {
    "metashape": (
        "Agisoft Metashape Professional",
        "USF_METASHAPE_LICENSE",
        "metashape.lic",
        "https://www.agisoft.com/buy/online/"),
    "rizomuv": (
        "RizomUV VS/RS",
        "USF_RIZOMUV_LICENSE",
        "rizomuv.lic",
        "https://www.rizom-lab.com/buy/"),
    "ue5": (
        "Unreal Engine 5",
        "USF_UE5_LICENSE",
        "ue5.lic",
        "https://www.unrealengine.com/licensing"),
}


class LicenseBlocked(RuntimeError):
    """许可证未满足, 拒绝执行（不是普通失败, 需向用户给出合规指引）。"""


# --------------------------------------------------------------------- #
#  基础判定
# --------------------------------------------------------------------- #
def is_commercial_use(settings=None) -> bool:
    """是否声明了商业用途（环境变量优先, 其次 settings.commercial_use）。"""
    env = os.environ.get(ENV_COMMERCIAL, "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    return bool(getattr(settings, "commercial_use", False))


def commercial_use_source(settings=None) -> str:
    env = os.environ.get(ENV_COMMERCIAL, "").strip()
    if env:
        return f"环境变量 {ENV_COMMERCIAL}={env}"
    if getattr(settings, "commercial_use", False):
        return "settings.commercial_use=true"
    return "未声明商用"


def _load_settings(settings=None):
    if settings is not None:
        return settings
    from app.models.settings import PipelineSettings
    return PipelineSettings.load()


def is_3dgs_acknowledged(settings=None) -> bool:
    if os.environ.get(ENV_ACCEPT_3DGS, "").strip().lower() in (
            "1", "true", "yes", "on"):
        return True
    return bool(getattr(settings, "license_ack_3dgs", False))


# --------------------------------------------------------------------- #
#  3.1 gaussian-splatting —— 商用阻断 + 不可跳过确认
# --------------------------------------------------------------------- #
def _print_3dgs_terms() -> None:
    print("\n" + "=" * 72)
    print("许可提示: gaussian-splatting (Inria / MPII)")
    print("  仓库: " + INRIA_REPO)
    print("  许可: " + INRIA_LICENSE)
    print("  该仓库仅授予非商业研究与评估用途; 任何商业用途（含内部生产、")
    print("  对外服务、产品集成）必须事先取得 Inria 商业授权:")
    print("  " + INRIA_COMMERCIAL)
    print("  继续即表示你确认本次使用属于非商业用途, 并自行承担合规责任。")
    print("=" * 72 + "\n")


def require_3dgs_license(settings=None, *, interactive: bool = False,
                         context: str = "3DGS 训练") -> None:
    """3DGS 执行前的许可证门。

    - 声明商用 → 立即 LicenseBlocked（不可绕过）;
    - 已确认（CLI 参数/GUI 勾选/环境变量）→ 记录审计日志并放行;
    - 未确认 → 交互式 TTY 提示一次并写回 settings; 非交互直接拒绝。
    """
    s = _load_settings(settings)

    if is_commercial_use(s):
        msg = (
            "拒绝执行 3DGS: 已声明商业用途（来源: "
            + commercial_use_source(s) + "）。\n"
            "gaussian-splatting 采用 Inria/MPII 非商业许可, 商业使用必须\n"
            "取得 Inria 商业授权: " + INRIA_COMMERCIAL + "\n"
            "替代方案: 将 settings.trainer 切换为 opensplat / nerfstudio /\n"
            "postshot（各自许可由用户自行确认）, 或用 --model 注入已授权的点云。")
        log.error("[LICENSE] %s", msg.replace("\n", " "))
        raise LicenseBlocked(msg)

    if is_3dgs_acknowledged(s):
        log.info("[LICENSE] %s: 已记录非商业用途确认 (settings.license_ack_3dgs"
                 "=%s, env=%s)", context,
                 bool(getattr(s, "license_ack_3dgs", False)),
                 os.environ.get(ENV_ACCEPT_3DGS, ""))
        return

    if interactive and sys.stdin.isatty():
        _print_3dgs_terms()
        try:
            answer = input("输入 YES 确认非商业用途并继续: ").strip()
        except EOFError:
            answer = ""
        if answer.upper() != "YES":
            raise LicenseBlocked(
                "用户未确认 Inria 非商业许可, 拒绝执行 3DGS。")
        try:
            s.license_ack_3dgs = True
            s.save()
        except OSError as exc:
            log.warning("[LICENSE] 确认状态写回 settings 失败: %s", exc)
        log.info("[LICENSE] %s: 用户交互确认非商业用途 (已写回 settings)",
                 context)
        return

    raise LicenseBlocked(
        "未确认 gaussian-splatting 的 Inria/MPII 非商业许可, 拒绝执行 "
        + context + "。\n"
        "CLI: 加 --accept-3dgs-license（或设 "
        + ENV_ACCEPT_3DGS + "=1）;\n"
        "GUI: 在「训练」区勾选非商业用途确认;\n"
        "商业用途请先取得 Inria 授权: " + INRIA_COMMERCIAL)


# --------------------------------------------------------------------- #
#  3.2 FFmpeg —— GPLv3 分发义务提示
# --------------------------------------------------------------------- #
def ffmpeg_gpl_notice() -> str:
    return (
        "FFmpeg (gyan.dev 构建) 采用 GPLv3。USF 不分发 FFmpeg 二进制,\n"
        "仅以子进程方式调用用户自行获取的 FFmpeg。若你选择由 USF 自动\n"
        "下载该构建, 该副本的 GPLv3 义务（源码可得性、同许可分发等）\n"
        "由你作为分发者承担; 商业闭源分发建议改用 LGPL 构建或自行获取。\n"
        "来源: https://www.gyan.dev/ffmpeg/builds/\n"
        "许可: https://www.gnu.org/licenses/gpl-3.0.html")


def confirm_ffmpeg_download(*, allow_silent: bool = False) -> bool:
    """自动下载 FFmpeg 前的 GPLv3 确认; 返回是否允许下载。"""
    if os.environ.get("USF_ACCEPT_FFMPEG_GPL", "").strip().lower() in (
            "1", "true", "yes", "on"):
        log.info("[LICENSE] FFmpeg 下载: 已通过环境变量确认 GPLv3 义务")
        return True
    if allow_silent:
        log.warning("[LICENSE] FFmpeg 下载: allow_silent_install=true 跳过确认, "
                    "GPLv3 义务仍由用户承担")
        return True
    print("\n" + ffmpeg_gpl_notice() + "\n")
    if sys.stdin.isatty():
        try:
            answer = input("输入 YES 同意自行承担 GPLv3 义务并下载: ").strip()
        except EOFError:
            answer = ""
        ok = answer.upper() == "YES"
        log.info("[LICENSE] FFmpeg 下载确认: %s", "已确认" if ok else "已拒绝")
        return ok
    log.warning("[LICENSE] FFmpeg 自动下载被拒绝: 非交互环境且未确认 GPLv3 "
                "(可设 USF_ACCEPT_FFMPEG_GPL=1 或手动安装)")
    return False


# --------------------------------------------------------------------- #
#  3.4 RizomUV / Metashape / UE5 —— 授权检测桩
# --------------------------------------------------------------------- #
def license_file_for(component: str) -> Optional[Path]:
    key = component.strip().lower()
    spec = OPTIONAL_COMPONENTS.get(key)
    if not spec:
        return None
    env_name, default_name = spec[1], spec[2]
    explicit = os.environ.get(env_name, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    lic_dir = os.environ.get(ENV_LICENSE_DIR, "").strip()
    if lic_dir:
        return Path(lic_dir).expanduser() / default_name
    return None


def detect_optional_license(component: str) -> tuple[bool, str]:
    """检测用户自备授权是否存在（只做文件/环境变量存在性检测）。

    返回 (ok, 说明)。不解析、不模拟、不绕过任何厂商许可证校验。
    """
    key = component.strip().lower()
    spec = OPTIONAL_COMPONENTS.get(key)
    if not spec:
        return False, f"未知组件: {component}"
    display, env_name, default_name, buy_url = spec
    path = license_file_for(key)
    if path is None:
        return False, (
            f"{display}: 未提供授权信息。请设置 {env_name}=<授权文件路径> "
            f"或 {ENV_LICENSE_DIR}=<目录>（目录下放 {default_name}）。"
            f"授权获取: {buy_url}")
    if path.exists():
        return True, f"{display}: 检测到用户自备授权文件 {path}"
    return False, (
        f"{display}: 授权文件不存在 {path}（设置 {env_name} / "
        f"{ENV_LICENSE_DIR}; 获取: {buy_url}）")


def require_optional_license(component: str) -> None:
    """可选商业组件执行前调用; 未检测到授权 → LicenseBlocked。

    调用方通常把 LicenseBlocked 转成 StageSkipped（跳过 + 记录原因）。
    """
    ok, detail = detect_optional_license(component)
    if ok:
        log.info("[LICENSE] %s", detail)
        return
    log.warning("[LICENSE] 跳过 %s: %s", component, detail)
    raise LicenseBlocked(detail)
