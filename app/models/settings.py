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

# pip 镜像表：键 → 索引 URL，none 就是官方直连。加源记得同步 PIP_MIRROR_LABELS
PIP_MIRRORS = {
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple/",
    "aliyun":   "https://mirrors.aliyun.com/pypi/simple/",
    "tencent":  "https://mirrors.cloud.tencent.com/pypi/simple/",
    "ustc":     "https://pypi.mirrors.ustc.edu.cn/simple/",
    "huawei":   "https://repo.huaweicloud.com/repository/pypi/simple/",
    "none":     "",  # 直接连官方，不绕镜像
}

# 下拉框里显示的名字，键必须跟 PIP_MIRRORS 对得上，不然会 KeyError
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
    frame_fps: float = 8.0            # 抽帧帧率。给到 8 是因为视角不够时，3DGS 出来的洞特别明显
    max_frames: int = 300             # 抽帧上限。一般 100~400 帧够用，再多是给显存找麻烦
    preprocess_max_side: int = 1600   # 预处理后的最长边，缩一缩省显存
    preprocess_workers: int = 8       # 预处理开几个线程

    # ---- SfM / 3DGS ----
    colmap_exe: Optional[str] = None  # 不填也行，探测不到会引导你去装
    ffmpeg_exe: Optional[str] = None  # 空着就先走 PATH，再不行用 tools_root 里的便携版
    gs_repo: str = str(resource_root() / "external" / "gaussian-splatting")
    gs_python: Optional[str] = None   # 训练环境的 python.exe；None 表示用当前解释器（只有源码运行会走到）
    gs_iterations: int = 30_000
    train_timeout_s: int = 14_400     # 训练墙钟上限，防止它挂在那儿一动不动

    # ---- 网格 ----
    poisson_depth: int = 10
    opacity_threshold: float = 0.10   # 高斯的透明度阈值。别调回 0.35：黑色高光主体会被滤没，只剩一层背景
                                      # 0.9.9 gai 过一次：默认从 0.35 降到 0.10，黑色主体不会再被滤光，别改回去
    density_quantile: float = 0.01    # 泊松按密度分位裁边，剪掉飘在外面的碎面
    texture_size: int = 4096          # 烘焙贴图分辨率。2048 拍近景就糊了，4096 起
    enable_tsdf: bool = True          # 走 3DGS 深度 → TSDF，质量优先；关掉就退回泊松
    lod_ratios: List[float] = field(default_factory=lambda: [1.0, 0.5, 0.25])

    # ---- DCC ----
    blender_exe: Optional[str] = None
    metashape_exe: Optional[str] = None
    maya_exe: Optional[str] = None
    max_exe: Optional[str] = None
    ue5_editor_cmd: Optional[str] = None
    ue5_uproject: Optional[str] = None
    # 下面这些是可手动覆盖的宿主路径，留空就走自动探测
    zbrush_exe: Optional[str] = None
    houdini_exe: Optional[str] = None
    c4d_exe: Optional[str] = None
    modo_exe: Optional[str] = None
    mudbox_exe: Optional[str] = None
    substance_exe: Optional[str] = None
    marmoset_exe: Optional[str] = None
    rhino_exe: Optional[str] = None
    # 第二批宿主，现在只做探测和路径管理，桥接后面版本再说
    coat_exe: Optional[str] = None
    rizomuv_exe: Optional[str] = None
    topogun_exe: Optional[str] = None
    # 输入侧的工具，不算导出 DCC，也就不进探测表
    premiere_exe: Optional[str] = None  # Premiere，抽帧面板要用
    enable_metashape_repair: bool = False  # 这个得 Professional 版才有脚本接口
    enable_cuda_accel: bool = True     # CUDA 加速只管 COLMAP 的特征提取和匹配
                                       # 训练那边本来就用 GPU，跟这个开关没关系
    enable_rizomuv: bool = True        # 自动展 UV，前提是机器上有 RizomUV
    enable_subject_mask: bool = False  # 主体遮罩（rembg），默认关着，开了会慢
    quality_preset: str = "standard"   # 质量预设：fast / standard / quality
    trainer: str = "builtin"           # 训练引擎：builtin / opensplat / nerfstudio
    realityscan_exe: Optional[str] = None  # RealityScan 的命令行
    opensplat_exe: Optional[str] = None    # OpenSplat 的 exe，可选
    postshot_cli: Optional[str] = None     # Postshot CLI，可选
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
        default_factory=lambda: ["fbx", "obj", "glb", "blend", "usd", "uasset"])
    export_dir: Optional[str] = None   # 导出位置。None 就自动放在输入源旁边的 usf_work/output
    bake_ao: bool = False              # 要不要顺手烘个 AO，可选，慢

    # ---- 依赖管理 / 安装行为 ----
    pip_mirror: str = "tsinghua"
    tools_root: str = str(Path.home() / ".universal_scene_forge" / "tools")
    allow_silent_install: bool = False  # 设 True 之后自动安装不再弹框，跑批处理省事

    # ---- 合规（LICENSE_COMPLIANCE_CHANGES.md 3.1）----
    commercial_use: bool = False        # 声明商用就直接拦，Inria 那套不能商用
    license_ack_3dgs: bool = False      # 记一下用户已经确认过非商业用途

    def save(self, path: Optional[Path] = None) -> None:
        """把当前配置写入 JSON；path 为空时写到默认配置位置。"""
        path = path or settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "PipelineSettings":
        """读取配置。文件缺失或解析失败时回退默认值，并对旧字段做兼容迁移。"""
        p = path or settings_file()
        if not p.exists():
            # 老版本把 settings 塞在安装目录里，冻结之后那儿不可写，搬一次
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
        # 0.9.9 修过一个质量坑：老默认值 0.35 / 0.02 / 9 会把黑色主体滤光
        # 只剩 13% 的高斯。只在用户还留着旧默认值时才动，改过的配置不碰
        if data.get("opacity_threshold") == 0.35:
            data["opacity_threshold"] = 0.10
        if data.get("density_quantile") == 0.02:
            data["density_quantile"] = 0.01
        if data.get("poisson_depth") == 9:
            data["poisson_depth"] = 10
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid})

    @property
    def mirror_url(self) -> str:
        """返回当前镜像源对应的索引地址，用于 pip 安装时指定 -i 参数。"""
        return PIP_MIRRORS.get(self.pip_mirror, "")
