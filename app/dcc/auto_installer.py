"""缺失组件自动安装器。

安装策略（安全合规设计）:
- 默认「确认后安装」: 下载前经 consent_cb 征得用户同意；
  settings.allow_silent_install=True 时才真正静默。
- 便携版优先（Blender/FFmpeg/COLMAP 官方 zip 解压到用户目录, 无需管理员）。
- 仅官方源: download.blender.org / gyan.dev / github.com/colmap。
- MSI 类安装包（如 Autodesk 系）不在自动安装范围, 仅给出引导。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable, List, Optional

import requests

from app.utils import gpu
from app.utils.logger import get_logger

log = get_logger("INSTALLER")

ConsentCb = Callable[[str], bool]                  # (软件名) -> 是否同意
ProgressCb = Callable[[int, int], None]            # (已下载字节, 总字节)

# 官方便携版下载源（版本可按需升级, 保持官方域名不变）
# urls 为回退序列: 依次尝试直到成功; Blender 官方源在部分地区返回 403,
# NLUUG/Clarkson 是 Blender 官方镜像网络成员; FFmpeg 备源为 gyan.dev 的 GitHub 发布镜像。
OFFICIAL_SOURCES = {
    "blender": {
        "urls": [
            "https://download.blender.org/release/Blender4.1/"
            "blender-4.1.1-windows-x64.zip",
            "https://ftp.nluug.nl/pub/graphics/blender/release/Blender4.1/"
            "blender-4.1.1-windows-x64.zip",
            "https://mirror.clarkson.edu/blender/release/Blender4.1/"
            "blender-4.1.1-windows-x64.zip",
        ],
        "exe_globs": ["blender-*/blender.exe", "blender.exe"],
    },
    "ffmpeg": {
        # gyan.dev 官方构建 + 其 GitHub Releases 官方镜像;
        # GitHub 直连在部分地区超时/限速, gh-proxy/ghfast 为加速镜像(同款回退)。
        "urls": [
            "https://www.gyan.dev/ffmpeg/builds/"
            "ffmpeg-release-essentials.zip",
            "https://github.com/GyanD/codexffmpeg/releases/download/7.1/"
            "ffmpeg-7.1-essentials_build.zip",
            "https://gh-proxy.com/https://github.com/GyanD/codexffmpeg/"
            "releases/download/7.1/ffmpeg-7.1-essentials_build.zip",
            "https://ghfast.top/https://github.com/GyanD/codexffmpeg/"
            "releases/download/7.1/ffmpeg-7.1-essentials_build.zip",
        ],
        "exe_globs": ["ffmpeg-*/bin/ffmpeg.exe", "bin/ffmpeg.exe"],
    },
    "colmap": {
        # GitHub 直连在部分地区超时/限速, gh-proxy/ghfast 为 GitHub Releases
        # 加速镜像(同一文件的转发), 兜底顺序: 官方 → gh-proxy → ghfast。
        # urls = CUDA 版(仅 NVIDIA); urls_cpu = 无 CUDA 版(AMD/Intel/核显,
        # SIFT 走 CPU, 速度较慢但功能完整) —— 按厂商自动选择。
        "urls": [
            "https://github.com/colmap/colmap/releases/download/3.9.1/"
            "COLMAP-3.9.1-windows-cuda.zip",
            "https://gh-proxy.com/https://github.com/colmap/colmap/releases/"
            "download/3.9.1/COLMAP-3.9.1-windows-cuda.zip",
            "https://ghfast.top/https://github.com/colmap/colmap/releases/"
            "download/3.9.1/COLMAP-3.9.1-windows-cuda.zip",
        ],
        "urls_cpu": [
            "https://github.com/colmap/colmap/releases/download/3.9.1/"
            "COLMAP-3.9.1-windows-no-cuda.zip",
            "https://gh-proxy.com/https://github.com/colmap/colmap/releases/"
            "download/3.9.1/COLMAP-3.9.1-windows-no-cuda.zip",
            "https://ghfast.top/https://github.com/colmap/colmap/releases/"
            "download/3.9.1/COLMAP-3.9.1-windows-no-cuda.zip",
        ],
        "exe_globs": ["COLMAP-*/COLMAP.bat", "COLMAP.bat"],
    },
}

CHUNK = 1 << 20  # 1MB


def find_portable_exe(key: str, tools_root: Path) -> Optional[Path]:
    """在 tools_root/<key> 下按 exe_globs 定位便携版 exe（模块级供 deps 体检复用）。"""
    spec = OFFICIAL_SOURCES.get(key)
    if not spec:
        return None
    root = Path(tools_root) / key
    if not root.exists():
        return None
    for pattern in spec["exe_globs"]:
        for candidate in root.rglob(pattern.split("/")[-1]):
            if candidate.is_file():
                for part in pattern.split("/")[:-1]:
                    if part != "*" and part not in candidate.parts:
                        break
                else:
                    return candidate
    return None


class AutoInstaller:
    def __init__(self, tools_root: Path, consent_cb: Optional[ConsentCb] = None,
                 progress_cb: Optional[ProgressCb] = None,
                 allow_silent: bool = False):
        self.tools_root = Path(tools_root)
        self.consent_cb = consent_cb
        self.progress_cb = progress_cb
        self.allow_silent = allow_silent

    # ================================================================== #
    def ensure(self, key: str, already_available: bool = False) -> Optional[Path]:
        """确保组件可用。返回 exe 路径; 无法/未同意安装时返回 None。"""
        if already_available or key not in OFFICIAL_SOURCES:
            return None
        installed = self._find_installed(key)
        if installed:
            log.info("%s 已就绪: %s", key, installed)
            return installed
        return self._install_portable(key)

    # ================================================================== #
    def _find_installed(self, key: str) -> Optional[Path]:
        """在 tools_root 下找已解压的 exe（跨启动会话复用）。"""
        return find_portable_exe(key, self.tools_root)

    # ================================================================== #
    def _install_portable(self, key: str) -> Optional[Path]:
        if not self.allow_silent:
            agreed = self.consent_cb(key) if self.consent_cb else False
            if not agreed:
                log.warning("用户拒绝安装 %s, 跳过（相关步骤将降级）", key)
                return None
        spec = OFFICIAL_SOURCES[key]
        dest_dir = self.tools_root / key
        dest_dir.mkdir(parents=True, exist_ok=True)
        zip_path = dest_dir / f"{key}.zip"

        # COLMAP 按显卡厂商选择版本(BUG-011): CUDA 版仅在 NVIDIA 可用,
        # AMD/Intel/核显下载 no-cuda 版本, SIFT 走 CPU。
        urls = spec["urls"]
        if key == "colmap" and not gpu.probe().is_nvidia \
                and spec.get("urls_cpu"):
            urls = spec["urls_cpu"]
            log.info("检测到非 NVIDIA 显卡, COLMAP 选用无 CUDA 版本（CPU SIFT）")

        downloaded = False
        last_exc: Optional[Exception] = None
        for i, url in enumerate(urls, start=1):
            try:
                log.info("下载 %s（源 %d/%d）← %s", key, i, len(urls), url)
                self._download(url, zip_path)
                downloaded = True
                break
            except (OSError, requests.RequestException) as exc:
                last_exc = exc
                log.warning("下载源 %d/%d 失败（%s）, 尝试下一个…",
                            i, len(urls), exc)
        if not downloaded:
            log.error("安装 %s 失败: 全部 %d 个下载源均不可用（最后错误: %s）",
                      key, len(urls), last_exc)
            zip_path.unlink(missing_ok=True)
            return None

        try:
            log.info("解压 %s → %s", zip_path.name, dest_dir)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest_dir)
        except (OSError, zipfile.BadZipFile) as exc:
            log.error("解压 %s 失败: %s", key, exc)
            return None
        finally:
            zip_path.unlink(missing_ok=True)

        exe = self._find_installed(key)
        if exe:
            log.info("%s 安装完成: %s", key, exe)
            return exe
        log.error("%s 解压后未找到可执行文件", key)
        return None

    # ================================================================== #
    def _download(self, url: str, dest: Path, timeout: int = 60) -> None:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=CHUNK):
                    f.write(chunk)
                    done += len(chunk)
                    if self.progress_cb:
                        self.progress_cb(done, total)
        if dest.stat().st_size < 1_000_000:
            raise OSError("下载文件异常过小, 疑似 404 页面")

    # ================================================================== #
    @staticmethod
    def elevate_and_run_msi(msi_path: Path, extra_args: str = "/qn") -> bool:
        """以管理员权限静默安装 MSI（UAC 弹窗确认, 由系统把关）。"""
        import ctypes
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "msiexec", f'/i "{msi_path}" {extra_args}', None, 0)
        return ret > 32
