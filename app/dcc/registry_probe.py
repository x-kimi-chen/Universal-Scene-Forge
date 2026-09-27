"""已安装 DCC 软件三级探测: 环境变量 → Windows 注册表 → 常见安装路径。

全部探测动作 try/except 包裹——探测失败返回 not available,
绝不因注册表项缺失/权限问题影响主流程。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app.utils.logger import get_logger

log = get_logger("PROBE")

try:
    import winreg  # noqa: F401  Windows 专属
    HAS_WINREG = True
except ImportError:
    HAS_WINREG = False

def _program_files_dirs() -> List[str]:
    """常见安装根目录(B-04): 环境变量优先, 再枚举所有盘符下的 Program Files。

    用户常把 DCC 装到 D 盘等其他盘, 仅扫 C 盘会漏检（如 D:\\Program Files\\Agisoft）。
    惰性求值: 每次 detect 时重算, 环境变化即时生效。"""
    dirs: List[str] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                "LOCALAPPDATA"):
        val = os.environ.get(var, "")
        if val and val not in dirs and Path(val).is_dir():
            dirs.append(val)
    for letter in "DEFGHIJKLMN":      # A/B 为软驱保留, C 已含于环境变量
        for name in ("Program Files", "Program Files (x86)"):
            d = Path(f"{letter}:\\") / name
            if d.is_dir() and str(d) not in dirs:
                dirs.append(str(d))
    return dirs


@dataclass
class DccInfo:
    key: str
    name: str
    available: bool
    exe: str = ""
    version: str = ""
    source: str = "not found"   # env | registry | path | settings


@dataclass
class DccSpec:
    key: str
    name: str
    exe_name: str
    env_vars: List[str] = field(default_factory=list)
    # (hive, 子键模式, 值名列表) — 子键支持版本号通配, 如 Autodesk/Maya/*
    registry: List[tuple] = field(default_factory=list)
    # exe 相对安装根的候选子路径
    sub_paths: List[str] = field(default_factory=list)
    # 常见安装根目录(相对 ProgramFiles)
    common_roots: List[str] = field(default_factory=list)
    # Steam 版: steamapps/common 下的目录名(如 "Blender"); None 表示不上 Steam
    steam_common: Optional[str] = None


SPECS: List[DccSpec] = [
    DccSpec(
        key="blender", name="Blender", exe_name="blender.exe",
        env_vars=["BLENDER_PATH", "BLENDER_EXE"],
        registry=[(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\BlenderFoundation",
                   ["InstallDir", "InstallLocation", "Path"])],
        sub_paths=["", "bin"],
        common_roots=[r"Blender Foundation\*"],
        steam_common="Blender",
    ),
    DccSpec(
        key="maya", name="Autodesk Maya", exe_name="maya.exe",
        env_vars=["MAYA_LOCATION", "MAYA_PATH"],
        registry=[(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Autodesk\Maya\*",
                   ["InstallLocation"])],
        sub_paths=["bin"],
        common_roots=[r"Autodesk\Maya*"],
    ),
    DccSpec(
        key="max", name="Autodesk 3ds Max", exe_name="3dsmax.exe",
        env_vars=["ADSK_3DSMAX_PATH"],
        registry=[(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Autodesk\3dsMax\*",
                   ["Installdir", "InstallLocation", "ProductInformation"])],
        sub_paths=[""],
        common_roots=[r"Autodesk\3ds Max *"],
    ),
    DccSpec(
        key="metashape", name="Agisoft Metashape Pro", exe_name="metashape.exe",
        env_vars=["METASHAPE_PATH", "AGISOFT_PATH"],
        sub_paths=[""],
        common_roots=[r"Agisoft\Metashape Pro", r"Agisoft\Metashape"],
    ),
    DccSpec(
        key="ue5", name="Unreal Engine 5", exe_name="UnrealEditor-Cmd.exe",
        env_vars=["UE5_ROOT"],
        registry=[(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\Unreal Engine\*",
                   ["InstalledDirectory"])],
        sub_paths=[r"Engine\Binaries\Win64"],
        common_roots=[r"Epic Games\UE_*"],
    ),
    # ---------------- 次世代建模软件扩展 ----------------
    DccSpec(
        key="zbrush", name="Maxon ZBrush", exe_name="ZBrush.exe",
        env_vars=["ZBRUSH_PATH", "ZBRUSH_BIN"],
        registry=[(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Pixologic",
                   ["InstallPath", "InstallLocation", "Path"])],
        sub_paths=[""],
        common_roots=[r"MAXON\ZBrush*", r"Pixologic\ZBrush*"],
    ),
    DccSpec(
        key="houdini", name="SideFX Houdini", exe_name="hython.exe",
        env_vars=["HFS", "HOUDINI_LOCATION"],
        registry=[(winreg.HKEY_LOCAL_MACHINE,
                   r"SOFTWARE\Side Effects Software\Houdini\*",
                   ["InstallPath", "InstallLocation"])],
        sub_paths=["bin"],
        common_roots=[r"Side Effects Software\Houdini*"],
    ),
    DccSpec(
        key="c4d", name="Maxon Cinema 4D", exe_name="Cinema 4D.exe",
        env_vars=["C4D_PATH", "CINEMA4D_PATH"],
        sub_paths=[""],
        common_roots=[r"Maxon Cinema 4D *", r"MAXON\CINEMA 4D *"],
    ),
    DccSpec(
        key="modo", name="Foundry Modo", exe_name="modo.exe",
        env_vars=["MODO_PATH"],
        sub_paths=[""],
        common_roots=[r"Foundry\Modo*"],
    ),
    DccSpec(
        key="mudbox", name="Autodesk Mudbox", exe_name="Mudbox.exe",
        env_vars=["MUDBOX_PATH"],
        registry=[(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Autodesk\Mudbox\*",
                   ["InstallLocation"])],
        sub_paths=["", "bin"],
        common_roots=[r"Autodesk\Mudbox*"],
    ),
    DccSpec(
        key="substance", name="Adobe Substance 3D Painter", exe_name="Adobe Substance 3D Painter.exe",
        env_vars=["SUBSTANCE_PAINTER_PATH"],
        sub_paths=[""],
        common_roots=[r"Adobe\Adobe Substance 3D Painter*",
                      r"Allegorithmic\Adobe Substance 3D Painter*"],
    ),
    DccSpec(
        key="marmoset", name="Marmoset Toolbag", exe_name="Toolbag.exe",
        env_vars=["TOOLBAG_PATH", "MARMOSET_PATH"],
        sub_paths=[""],
        common_roots=[r"Marmoset\Toolbag*"],
    ),
    DccSpec(
        key="rhino", name="Rhinoceros 3D", exe_name="Rhino.exe",
        env_vars=["RHINO_PATH"],
        registry=[(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\McNeel\Rhinoceros\*",
                   ["InstallPath"])],
        sub_paths=["System"],
        common_roots=[r"Rhino *"],
    ),
    # ---------------- 次世代建模软件扩展 II (v0.9.1) ----------------
    DccSpec(
        key="coat", name="3D-Coat", exe_name="3D-Coat.exe",
        env_vars=["COAT3D_PATH", "3DCOAT_PATH"],
        registry=[(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Pilgway\*",
                   ["InstallPath", "InstallLocation", "Path"])],
        sub_paths=[""],
        common_roots=[r"3D-Coat*", r"Pilgway\3D-Coat*"],
    ),
    DccSpec(
        key="rizomuv", name="RizomUV", exe_name="rizomuv.exe",
        env_vars=["RIZOMUV_PATH"],
        sub_paths=[""],
        common_roots=[r"Rizom Lab\RizomUV*"],
    ),
    DccSpec(
        key="topogun", name="TopoGun", exe_name="TopoGun.exe",
        env_vars=["TOPOGUN_PATH"],
        sub_paths=[""],
        common_roots=[r"TopoGun*"],
    ),
]


# (探测 key, 显示名, settings 字段名) — 「项目调用路径」设置对话框与
# PipelineController 共用的数据源(B-04: CLI/GUI 探测同源同覆盖)
DCC_PATH_FIELDS = [
    ("blender",   "Blender",                  "blender_exe"),
    ("max",       "Autodesk 3ds Max",         "max_exe"),
    ("maya",      "Autodesk Maya",            "maya_exe"),
    ("zbrush",    "Maxon ZBrush",             "zbrush_exe"),
    ("houdini",   "SideFX Houdini (hython)",  "houdini_exe"),
    ("c4d",       "Maxon Cinema 4D",          "c4d_exe"),
    ("modo",      "Foundry Modo",             "modo_exe"),
    ("mudbox",    "Autodesk Mudbox",          "mudbox_exe"),
    ("substance", "Adobe Substance 3D",       "substance_exe"),
    ("marmoset",  "Marmoset Toolbag",         "marmoset_exe"),
    ("rhino",     "Rhinoceros 3D",            "rhino_exe"),
    ("metashape", "Agisoft Metashape Pro",    "metashape_exe"),
    ("ue5",       "Unreal Engine 5",          "ue5_editor_cmd"),
    # 次世代建模软件扩展 II（探测 + 路径管理）
    ("coat",      "3D-Coat",                  "coat_exe"),
    ("rizomuv",   "RizomUV",                  "rizomuv_exe"),
    ("topogun",   "TopoGun",                  "topogun_exe"),
]


class DccDetector:
    def __init__(self, overrides: Optional[dict] = None):
        self.overrides = {k: v for k, v in (overrides or {}).items() if v}

    # ------------------------------------------------------------------ #
    def detect_all(self) -> dict:
        result = {}
        for spec in SPECS:
            if spec.key in self.overrides:
                exe = self._locate_exe(self.overrides[spec.key], spec)
                result[spec.key] = DccInfo(
                    spec.key, spec.name, exe is not None,
                    exe=exe or "", source="settings")
                continue
            result[spec.key] = self._detect(spec)
        for key, info in result.items():
            # ASCII 标记: GBK 控制台/日志采集下 Unicode 符号会显示成 \u2718 转义(C-06)
            state = "[OK] " + (info.exe or "未找到") if info.available else "[X] 未安装"
            log.info("探测 %-10s %-12s %s", key, info.source, state)
        return result

    # ------------------------------------------------------------------ #
    def _detect(self, spec: DccSpec) -> DccInfo:
        # 1) 环境变量
        for var in spec.env_vars:
            val = os.environ.get(var)
            if val:
                exe = self._locate_exe(val, spec)
                if exe:
                    return DccInfo(spec.key, spec.name, True, exe=exe,
                                   version=Path(val).name, source=f"env:{var}")
        # 2) 注册表
        if HAS_WINREG:
            for hive, pattern, value_names in spec.registry:
                exe, version = self._registry_lookup(hive, pattern, value_names)
                if exe:
                    located = self._locate_exe(exe, spec)
                    if located:
                        return DccInfo(spec.key, spec.name, True, exe=located,
                                       version=version, source="registry")
        # 3) Steam 库（Steam 版 Blender 等）
        exe = self._steam_lookup(spec)
        if exe:
            return DccInfo(spec.key, spec.name, True, exe=exe, source="steam")
        # 4) 常见路径 glob
        exe, version = self._common_path_lookup(spec)
        if exe:
            return DccInfo(spec.key, spec.name, True, exe=exe,
                           version=version, source="path")
        return DccInfo(spec.key, spec.name, False)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _locate_exe(root: str, spec: "DccSpec") -> Optional[str]:
        """root 可能是 exe 全路径或安装根目录；在根目录及候选子目录中找 exe。"""
        root_path = Path(root)
        if root_path.is_file():
            return str(root_path)
        for sub in spec.sub_paths or [""]:
            exe = root_path / sub / spec.exe_name
            if exe.exists():
                return str(exe)
        return None

    # ------------------------------------------------------------------ #
    @staticmethod
    def _registry_lookup(hive, subkey_pattern: str, value_names: List[str]):
        """读取注册表安装路径。

        模式分两类:
        - 无通配 (SOFTWARE\\BlenderFoundation): 先读父键命名值, 再枚举子键读值
        - 通配   (SOFTWARE\\Autodesk\\Maya\\*):  枚举前缀匹配子键读值
        返回 (install_dir, version)。
        """
        import winreg
        base, _, wildcard = subkey_pattern.partition("\\*")
        parent = base.rstrip("\\")

        def _subkeys(key) -> List[str]:
            names = []
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, i))
                except OSError:
                    return names
                i += 1

        try:
            with winreg.OpenKey(hive, parent, 0, winreg.KEY_READ) as key:
                subs = _subkeys(key)
        except OSError:
            return None, None

        if wildcard:
            prefix = wildcard.rstrip("*").rstrip("\\")
            targets = [f"{parent}\\{s}" for s in subs if s.startswith(prefix)]
        else:
            targets = [parent] + [f"{parent}\\{s}" for s in subs]

        for full in targets:
            for value_name in value_names:
                try:
                    with winreg.OpenKey(hive, full, 0, winreg.KEY_READ) as k:
                        val, _ = winreg.QueryValueEx(k, value_name)
                        if isinstance(val, str) and val.strip():
                            return val, full.split("\\")[-1]
                except OSError:
                    continue
        return None, None

    # ------------------------------------------------------------------ #
    @staticmethod
    def _steam_lookup(spec: "DccSpec") -> Optional[str]:
        """Steam 库扫描: SteamPath 注册表 → libraryfolders.vdf → steamapps/common。

        vdf 行格式: "path"  "D:\\Program Files (x86)\\Steam"
        支持 Steam 主库之外的多个挂载盘。
        """
        if not HAS_WINREG or not spec.steam_common:
            return None
        import winreg
        libs: List[str] = []
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r"Software\Valve\Steam", 0,
                                    winreg.KEY_READ) as k:
                    val, _ = winreg.QueryValueEx(k, "SteamPath")
                    if isinstance(val, str) and val.strip() and val not in libs:
                        libs.append(val.strip())
            except OSError:
                continue
        for root in list(libs):
            vdf = Path(root) / "steamapps" / "libraryfolders.vdf"
            try:
                for line in vdf.read_text(
                        encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line.startswith('"path"'):
                        parts = line.split('"')
                        if len(parts) >= 4:
                            p = parts[3].replace("\\\\", "\\").strip()
                            if p and p not in libs:
                                libs.append(p)
            except OSError:
                continue
        for root in libs:
            base = Path(root) / "steamapps" / "common" / spec.steam_common
            for sub in spec.sub_paths or [""]:
                exe = base / sub / spec.exe_name
                if exe.exists():
                    return str(exe)
        return None

    # ------------------------------------------------------------------ #
    @staticmethod
    def _common_path_lookup(spec: DccSpec):
        for root in _program_files_dirs():
            if not root:
                continue
            for pattern in spec.common_roots:
                for candidate_root in Path(root).glob(pattern):
                    if not candidate_root.is_dir():
                        continue
                    for sub in spec.sub_paths or [""]:
                        exe = candidate_root / sub / spec.exe_name
                        if exe.exists():
                            return str(exe), candidate_root.name
        # 最后: PATH
        which = shutil.which(spec.exe_name)
        if which:
            return which, ""
        return None, None


def find_ue5_uproject(ue5_root: str, extra_dirs: List[str]) -> Optional[str]:
    """自动发现 UE5 工程（*.uproject）: 额外目录 → 文档/Unreal Projects → 源码根。"""
    search_dirs = list(extra_dirs)
    docs = Path.home() / "Documents" / "Unreal Projects"
    if docs.exists():
        search_dirs.append(str(docs))
    for d in search_dirs:
        try:
            for p in Path(d).rglob("*.uproject"):
                return str(p)
        except OSError:
            continue
    return None
