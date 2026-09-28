"""流水线配置模型。

职责:
- 集中管理全部可调参数 (dataclass, 类型安全)
- settings.json 持久化 (load / save)
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from app.utils.paths import resource_root
from app.utils.logger import get_logger

log = get_logger("SETTINGS")


def settings_file() -> Path:
    """settings.json 位置。

    - 源码运行: 项目根（开发调试方便）
    - 冻结模式: ~/.universal_scene_forge/settings.json —— 安装目录通常位于
      Program Files, 普通权限不可写, 写入会 PermissionError 导致工具路径
      装完即丢; 与 python_pkgs/tools 同置于用户主目录。
    """
    if getattr(sys, "frozen", False):
        return Path.home() / ".universal_scene_forge" / "settings.json"
    return resource_root() / "settings.json"


def _legacy_settings_file() -> Path:
    """旧版（≤0.9.2）冻结模式的 settings 位置（安装目录）, 仅用于迁移读取。"""
    return resource_root() / "settings.json"

# pip 镜像源（键 → 索引 URL; none = 官方直连; 下载源可按需增删, 显示名同步 PIP_MIRROR_LABELS）
PIP_MIRRORS = {
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "aliyun":   "https://mirrors.aliyun.com/pypi/simple/",
    "tencent":  "https://mirrors.cloud.tencent.com/pypi/simple/",
    "ustc":     "https://pypi.mirrors.ustc.edu.cn/simple/",
    "huawei":   "https://repo.huaweicloud.com/repository/pypi/simple/",
    "none":     "",  # 直连官方 PyPI
}

# 镜像下拉框显示名（键集须与 PIP_MIRRORS 保持一致）
PIP_MIRROR_LABELS = {
    "tsinghua": "清华 TUNA",
    "aliyun":   "阿里云",
    "tencent":  "腾讯云",
    "ustc":     "中科大 USTC",
    "huawei":   "华为云",
    "none":     "官方 PyPI（直连）",
}


@dataclass
class PipelineSettings:
    # ---- 输入抽取 ----
    frame_fps: float = 2.0            # 视频抽帧帧率
    max_frames: int = 300             # 抽帧上限（3DGS 常规 100~400 帧足够）
    preprocess_max_side: int = 1600   # 预处理最长边像素（降采样省显存）
    preprocess_workers: int = 8       # CPU 预处理线程数

    # ---- SfM / 3DGS ----
    colmap_exe: Optional[str] = None  # 留空则由探测+自动安装解决
    ffmpeg_exe: Optional[str] = None  # 留空 = PATH / tools_root 便携版
    gs_repo: str = str(resource_root() / "external" / "gaussian-splatting")
    gs_python: Optional[str] = None   # 训练环境 python.exe；None=当前解释器
    gs_iterations: int = 30_000

    # ---- 网格 ----
    poisson_depth: int = 9
    opacity_threshold: float = 0.35   # 高斯不透明度过滤阈值
    density_quantile: float = 0.02    # 泊松密度下分位裁剪
    lod_ratios: List[float] = field(default_factory=lambda: [1.0, 0.5, 0.25])

    # ---- DCC ----
    blender_exe: Optional[str] = None
    metashape_exe: Optional[str] = None
    maya_exe: Optional[str] = None
    max_exe: Optional[str] = None
    ue5_editor_cmd: Optional[str] = None
    ue5_uproject: Optional[str] = None
    # 次世代建模软件路径覆盖（留空 = 三级自动探测）
    zbrush_exe: Optional[str] = None
    houdini_exe: Optional[str] = None
    c4d_exe: Optional[str] = None
    modo_exe: Optional[str] = None
    mudbox_exe: Optional[str] = None
    substance_exe: Optional[str] = None
    marmoset_exe: Optional[str] = None
    rhino_exe: Optional[str] = None
    # 次世代建模软件扩展 II（探测 + 路径管理，深度桥接后续版本接入）
    coat_exe: Optional[str] = None
    rizomuv_exe: Optional[str] = None
    topogun_exe: Optional[str] = None
    # 输入侧工具（非导出 DCC, 不计入 DCC 探测网格）
    premiere_exe: Optional[str] = None  # Adobe Premiere Pro, 高效抽帧用
    enable_metashape_repair: bool = False  # 需 Metashape Professional 授权
    enable_cuda_accel: bool = True     # CUDA 加速: COLMAP 特征提取/匹配走 GPU
                                       # （3DGS 训练本身始终用 GPU, 不受此项影响）
    enable_rizomuv: bool = True        # RizomUV 自动展 UV (需安装 RizomUV VS/RS)
    enable_subject_mask: bool = False  # 0.9.6: 主体遮罩 (rembg), 预览开关默认关
    trainer: str = "builtin"           # 0.9.6: builtin | opensplat | nerfstudio
    realityscan_exe: Optional[str] = None  # 0.9.6: RealityScan/RealityCapture CLI
    rizomuv_timeout_s: int = 1800
    modo_timeout_s: int = 3600
    enable_ue5_preview: bool = True
    blender_timeout_s: int = 3600
    metashape_timeout_s: int = 14_400
    maya_timeout_s: int = 3600
    max_timeout_s: int = 3600
    ue5_timeout_s: int = 900
    houdini_timeout_s: int = 1800
    c4d_timeout_s: int = 1800

    # ---- 导出 ----
    export_formats: List[str] = field(
        default_factory=lambda: ["fbx", "obj", "glb", "blend", "uasset"])
    export_dir: Optional[str] = None   # 导出位置: None = 自动(输入源旁 usf_work/output)
    bake_ao: bool = False              # Blender 内 Cycles AO 烘焙（可选）

    # ---- 依赖管理 / 安装行为 ----
    pip_mirror: str = "tsinghua"
    tools_root: str = str(Path.home() / ".universal_scene_forge" / "tools")
    allow_silent_install: bool = False  # True 时自动安装不再弹确认框

    def save(self, path: Optional[Path] = None) -> None:
        path = path or settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "PipelineSettings":
        p = path or settings_file()
        if not p.exists():
            # 冻结模式一次性迁移: 旧版装在安装目录（不可写）的 settings
            if getattr(sys, "frozen", False):
                legacy = _legacy_settings_file()
                if legacy.exists() and legacy.resolve() != p.resolve():
                    try:
                        migrated = cls._from_json(
                            legacy.read_text(encoding="utf-8"))
                        migrated.save(p)
                        log.info("已迁移旧版 settings → %s", p)
                        return migrated
                    except (OSError, ValueError):
                        pass
            return cls()
        try:
            return cls._from_json(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError):
            return cls()

    @classmethod
    def _from_json(cls, text: str) -> "PipelineSettings":
        data = json.loads(text)
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid})

    @property
    def mirror_url(self) -> str:
        return PIP_MIRRORS.get(self.pip_mirror, "")
