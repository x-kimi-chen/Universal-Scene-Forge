"""依赖体检与一键安装（Python 库 + 系统工具 + 训练环境 + 镜像源适配）。"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from app.models.settings import PIP_MIRRORS, PipelineSettings
from app.utils.logger import get_logger

log = get_logger("DEPS")

# 训练环境 torch+CUDA 索引（多源回退: 官方源不可达时切交大镜像整库镜像）。
# cu128 覆盖 Volta~Hopper 及 Blackwell(RTX 50 系, sm_120), 对驱动兼容面最广。
TORCH_CU_TAG = "cu128"
TORCH_INDEX_OFFICIAL = f"https://download.pytorch.org/whl/{TORCH_CU_TAG}"
TORCH_INDEX_MIRROR = f"https://mirror.sjtu.edu.cn/pytorch-wheels/{TORCH_CU_TAG}/"

# GUI 壳必须有的 Python 库（打包后通常已内嵌）
REQUIRED_PY = {
    "PySide6": "PySide6",
    "numpy": "numpy",
    "cv2": "opencv-python-headless",
    "plyfile": "plyfile",
    "requests": "requests",
    "open3d": "open3d",
}

# 建议存在的系统工具（探测不到时由 auto_installer 补齐）
REQUIRED_TOOLS = ["ffmpeg", "colmap"]


@dataclass
class DepStatus:
    name: str
    kind: str          # python | tool | cuda
    ok: bool
    detail: str = ""
    fix_hint: str = ""
    package: str = ""  # pip 包名（kind=python 时）


def check_python() -> List[DepStatus]:
    results = []
    for mod, pkg in REQUIRED_PY.items():
        found = importlib.util.find_spec(mod) is not None
        results.append(DepStatus(
            name=pkg, kind="python", ok=found,
            detail="可导入" if found else "未安装",
            fix_hint=f"pip install {pkg}", package=pkg))
    return results


def check_tools(settings: Optional[PipelineSettings] = None) -> List[DepStatus]:
    """系统工具体检: 设置覆盖 → PATH → tools_root 便携版（显式设置优先, 与 SfmRunner 一致）。"""
    from app.dcc.auto_installer import find_portable_exe
    results = []
    for tool in REQUIRED_TOOLS:
        path = None
        if settings is not None:
            override = getattr(settings, f"{tool}_exe", None)
            if override and Path(override).exists():
                path = str(override)
        if not path:
            path = shutil.which(tool)
        if not path and settings is not None:
            portable = find_portable_exe(tool, Path(settings.tools_root))
            if portable:
                path = str(portable)
        results.append(DepStatus(
            name=tool, kind="tool", ok=path is not None,
            detail=str(path) if path else "未在 PATH 中找到",
            fix_hint="使用「安装缺失工具」多源回退自动下载官方便携版"))
    return results


def check_cuda() -> List[DepStatus]:
    """GPU 驱动体检(B-03): 与 gpu.probe() 同源验证 NVML 真实可用性。

    仅凭 nvidia-smi 存在会误报——驱动未加载 / 设备被沙箱屏蔽 / 集成显卡
    机器上 exe 在而查询失败, 用户却被提示「CUDA 可用」, 训练实跑 CPU 模式。
    """
    from app.utils import gpu
    info = gpu.probe()
    if info.available:
        return [DepStatus(name="CUDA 驱动", kind="cuda", ok=True,
                          detail=f"{info.name} | {info.vram_mb}MB | 驱动 {info.driver}")]
    has_smi = shutil.which("nvidia-smi") is not None
    detail = ("nvidia-smi 存在但查询失败（驱动未加载/设备被屏蔽）" if has_smi
              else "未检测到 NVIDIA 驱动")
    return [DepStatus(name="CUDA 驱动", kind="cuda", ok=False, detail=detail,
                      fix_hint="3DGS 训练将退化 CPU 模式；请安装/更新 NVIDIA 驱动")]


def training_python(settings: PipelineSettings) -> Optional[Path]:
    """定位 3DGS 训练解释器: 显式 gs_python 优先, 其次仓库内 .venv。"""
    if settings.gs_python:
        p = Path(settings.gs_python)
        return p if p.exists() else None
    venv_py = Path(settings.gs_repo) / ".venv" / "Scripts" / "python.exe"
    return venv_py if venv_py.exists() else None


def check_training_env(settings: PipelineSettings) -> List[DepStatus]:
    """3DGS 训练环境体检（快速部分: 仓库 + venv 存在性）。

    torch/CUDA 探测较慢（子进程导入 5–60s）, 单独放 check_training_torch(),
    由调用方放后台线程执行, 避免 GUI 卡顿。
    """
    repo = Path(settings.gs_repo)
    has_repo = (repo / "train.py").exists()
    results = [DepStatus(
        name="gaussian-splatting 仓库", kind="train", ok=has_repo,
        detail="train.py 就绪" if has_repo else "未找到 train.py",
        fix_hint="" if has_repo
        else "重装 USF（external/gaussian-splatting 随包分发）或 git clone --recursive")]

    py = training_python(settings)
    if py is None:
        results.append(DepStatus(
            name="训练环境 venv", kind="train", ok=False,
            detail="未创建（torch+CUDA 体积大, 不随安装包分发）",
            fix_hint="初始化命令见下方控制台"))
    else:
        results.append(DepStatus(
            name="训练环境 venv", kind="train", ok=True, detail=str(py)))
    return results


def check_training_torch(settings: PipelineSettings) -> DepStatus:
    """探测训练 venv 内 torch 与 CUDA（子进程导入, 慢, 放后台线程调用）。"""
    py = training_python(settings)
    name = "训练环境 torch+CUDA"
    if py is None:
        return DepStatus(name=name, kind="train", ok=False,
                         detail="环境未创建",
                         fix_hint="先创建训练 venv, 命令见下方控制台")
    try:
        out = subprocess.run(
            [str(py), "-c",
             "import torch; print(torch.__version__); "
             "print('cuda' if torch.cuda.is_available() else 'cpu')"],
            capture_output=True, text=True, timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError) as exc:
        return DepStatus(name=name, kind="train", ok=False,
                         detail=f"探测失败: {exc}",
                         fix_hint="检查训练 venv 完整性, 必要时删除后按命令重建")
    if out.returncode != 0:
        return DepStatus(name=name, kind="train", ok=False,
                         detail="torch 未安装",
                         fix_hint="安装命令见下方控制台")
    lines = [l.strip() for l in (out.stdout or "").splitlines() if l.strip()]
    version = lines[0] if lines else "?"
    cuda = len(lines) > 1 and "cuda" in lines[1].lower()
    return DepStatus(
        name=name, kind="train", ok=True,
        detail=f"torch {version}（{'CUDA 可用' if cuda else '仅 CPU, 训练极慢'}）",
        fix_hint="" if cuda
        else "当前为 CPU 版 torch; 建议按控制台命令重装 cu128 版本")


def training_setup_text(settings: PipelineSettings) -> str:
    """训练环境初始化命令块（手动兜底; 一键安装见 training_install 流程）。

    torch 用 cu128 源: 覆盖 Volta~Hopper 及 Blackwell(RTX 50 系, sm_120),
    且 CUDA 12.x 轮子对驱动兼容面最广; cu121 不支持 Blackwell。
    venv 固定 3.12: 与 USF 内核同版, torch 轮子覆盖最稳。
    """
    repo = Path(settings.gs_repo)
    py = repo / ".venv" / "Scripts" / "python.exe"
    sub = repo / "submodules"
    interp = " ".join(venv_interpreter() or ["py", "-3.12"])
    return (
        "cd /d " + str(repo) + "\n"
        + interp + " -m venv .venv\n"
        + str(py) + " -m pip install torch torchvision"
        " --index-url " + TORCH_INDEX_OFFICIAL + "\n"
        + str(py) + " -m pip install "
        + " ".join(f'"{sub / m}"' for m in
                   ("diff-gaussian-rasterization", "simple-knn", "fused-ssim")))


def torch_install_steps(settings: PipelineSettings) -> List[Tuple[str, List[str]]]:
    """训练环境 torch+CUDA 安装步骤序列（(标题, 命令), 依次尝试直到成功）。

    步骤 1 官方源; 步骤 2 交大镜像（cu128 整库 PEP 503 镜像, 国内网络
    官方源超时/被墙时的等价回退, 含 torch 全部依赖, 无需额外索引）。
    """
    py = str(Path(settings.gs_repo) / ".venv" / "Scripts" / "python.exe")
    return [
        (f"torch+CUDA 官方源（{TORCH_CU_TAG}）",
         [py, "-m", "pip", "install", "torch", "torchvision",
          "--index-url", TORCH_INDEX_OFFICIAL]),
        ("torch+CUDA 交大镜像（官方源失败自动回退）",
         [py, "-m", "pip", "install", "torch", "torchvision",
          "--index-url", TORCH_INDEX_MIRROR]),
    ]


def submodules_install_cmd(settings: PipelineSettings) -> List[str]:
    """三个 CUDA 子模块编译安装命令（diff-gaussian-rasterization 等）。"""
    repo = Path(settings.gs_repo)
    py = repo / ".venv" / "Scripts" / "python.exe"
    subs = [repo / "submodules" / m for m in
            ("diff-gaussian-rasterization", "simple-knn", "fused-ssim")]
    return [str(py), "-m", "pip", "install"] + [str(s) for s in subs]


def check_msvc() -> DepStatus:
    """VS C++ 生成工具探测: 编译 CUDA 子模块必需（vswhere → PATH 上的 cl）。"""
    name = "VS C++ 生成工具"
    pf86 = os.environ.get("ProgramFiles(x86)", "")
    vswhere = (Path(pf86) / "Microsoft Visual Studio" / "Installer"
               / "vswhere.exe") if pf86 else None
    if vswhere and vswhere.is_file():
        out = _run_capture(
            [str(vswhere), "-latest", "-products", "*",
             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"], timeout=20)
        if out and out.strip():
            return DepStatus(name=name, kind="train", ok=True,
                             detail=out.strip())
    cl = shutil.which("cl")
    if cl:
        return DepStatus(name=name, kind="train", ok=True, detail=cl)
    return DepStatus(name=name, kind="train", ok=False, detail="未找到",
                     fix_hint="编译 CUDA 子模块需要 VS Build Tools 2022"
                              "（安装时勾选「使用 C++ 的桌面开发」）")


def check_cuda_toolkit() -> DepStatus:
    """CUDA Toolkit (nvcc) 探测: 编译子模块必需（与显卡驱动是两回事）。"""
    name = "CUDA Toolkit"
    cuda_path = os.environ.get("CUDA_PATH", "")
    if cuda_path:
        nvcc = Path(cuda_path) / "bin" / "nvcc.exe"
        if nvcc.is_file():
            return DepStatus(name=name, kind="train", ok=True, detail=str(nvcc))
    nvcc = shutil.which("nvcc")
    if nvcc:
        return DepStatus(name=name, kind="train", ok=True, detail=nvcc)
    return DepStatus(name=name, kind="train", ok=False,
                     detail="未找到 CUDA_PATH / nvcc",
                     fix_hint="编译 CUDA 子模块需安装 CUDA Toolkit"
                              "（developer.nvidia.com, 版本与显卡驱动匹配即可）")


def run_streamed(cmd: List[str], emit: Callable[[str], None],
                 env: Optional[dict] = None,
                 cwd: Optional[str] = None) -> int:
    """运行子进程并逐行流式转发输出; 返回退出码（启动失败返回非 0）。

    env 为增量覆盖（在 os.environ 基础上合并）, 供子模块编译步骤注入
    VSLANG=1033 等变量。不设超时: torch 等大包慢速下载期可能长时间
    无输出, 误杀代价高于挂起（网络故障时 pip 自身的重试/超时会兜底）。
    """
    merged = None
    if env:
        merged = os.environ.copy()
        merged.update(env)
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=merged, cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except OSError as exc:
        emit(f"✘ 启动失败: {exc}")
        return 127
    for line in proc.stdout or []:
        emit(line.rstrip())
    return proc.wait()


def full_report(settings: Optional[PipelineSettings] = None) -> List[DepStatus]:
    return check_python() + check_tools(settings) + check_cuda()


def missing(report: List[DepStatus]) -> List[DepStatus]:
    return [d for d in report if not d.ok]


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结壳内（sys.executable 是 exe 而非解释器）。"""
    return getattr(sys, "frozen", False)


def _run_capture(cmd: List[str], timeout: int = 15) -> Optional[str]:
    """运行命令取 stdout 文本; 失败/非零退出码返回 None。"""
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _interp_ok(cmd: List[str]) -> bool:
    try:
        out = subprocess.run(
            cmd + ["--version"], capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_LAUNCHER_EXE_RE = re.compile(r"([A-Za-z]:\\[^\s]+?python\.exe)")


def _parse_launcher_pythons(text: str) -> List[str]:
    """从 py -0p 输出提取解释器 exe 路径。

    覆盖 uv 管理的非标准注册名（如 -V:Astral\\CPython3.12.14,
    py -3.12 命不中, 但路径可直接调用）。
    """
    return _LAUNCHER_EXE_RE.findall(text or "")


def _launcher_python_paths() -> List[str]:
    launcher = shutil.which("py")
    if not launcher:
        return []
    return _parse_launcher_pythons(_run_capture([launcher, "-0p"]) or "")


def _parse_version(text: str) -> Optional[str]:
    m = re.search(r"Python (\d+\.\d+)", text or "")
    return m.group(1) if m else None


def _interp_version(cmd: List[str]) -> Optional[str]:
    """解释器实际版本（major.minor）; 探测失败返回 None。"""
    return _parse_version(_run_capture(cmd + ["--version"], timeout=10) or "")


def _candidate_pythons() -> List[str]:
    """可能可用的解释器 exe 候选（py 注册列表 + 常见安装目录, 去重保序）。"""
    cands: List[str] = list(_launcher_python_paths())
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        cands += [str(p) for p in
                  Path(local).glob(r"Programs\Python\Python*\python.exe")]
    cands += [str(p) for p in
              (Path.home() / "AppData" / "Roaming" / "uv" / "python")
              .glob("cpython-*/python.exe")]
    seen: set = set()
    uniq = []
    for c in cands:
        key = c.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def pip_interpreter() -> Optional[List[str]]:
    """pip 可用解释器命令前缀; 找不到时返回 None。

    源码模式恒为当前解释器; 冻结模式 exe 不能当解释器用（-m pip 会被
    argparse 吞掉）, 且必须与冻结壳版本一致（C 扩展 ABI 匹配）:
    1. py -<ver>（python.org 标准注册机器）;
    2. py -0p 注册列表逐个试版本（uv 注册名非 3.12 形式时 py -3.12 命不中,
       但注册表里路径可用）;
    3. 常见安装目录扫描（LOCALAPPDATA/uv, 未注册场景兜底）。
    版本不一致的解释器一律不用——装出的 wheel 冻结壳无法导入,
    宁可返回 None 让 GUI 引导安装正确版本。
    """
    if not is_frozen():
        return [sys.executable]
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    launcher = shutil.which("py")
    if launcher and _interp_ok([launcher, f"-{ver}"]):
        return [launcher, f"-{ver}"]
    for exe in _candidate_pythons():
        if _interp_version([exe]) == ver:
            return [exe]
    return None


def venv_interpreter() -> Optional[List[str]]:
    """创建训练 venv 用的解释器: 版本匹配优先, 任意可用兜底。

    训练 venv 独立进程运行, 与冻结壳 ABI 无关, 版本可放宽;
    但仍优先与内核同版（3.12）, torch 轮子覆盖最稳。
    """
    strict = pip_interpreter()
    if strict is not None:
        return strict
    for name in ("py", "python"):
        exe = shutil.which(name)
        if exe and _interp_ok([exe]):
            return [exe]
    return None


def pip_cmd(packages: List[str], mirror_key: str = "tsinghua",
            python_exe: Optional[str] = None) -> List[str]:
    """构造 pip install 命令（纯函数, 供测试与 pip_install 复用）。

    冻结模式: 系统解释器 + --target 到安装目录 python_pkgs/（由
    paths.register_external_pkgs 纳入 sys.path, 冻结壳即可导入补装的库）。
    """
    if python_exe:
        cmd = [python_exe]
    else:
        cmd = pip_interpreter() or [sys.executable]
    cmd = list(cmd) + ["-m", "pip", "install", "--upgrade"]
    if is_frozen():
        from app.utils.paths import external_pkgs_dir
        cmd += ["--target", str(external_pkgs_dir())]
    mirror = PIP_MIRRORS.get(mirror_key, "")
    if mirror:
        cmd += ["-i", mirror]
    return cmd + packages


def pip_install(packages: List[str], mirror_key: str = "tsinghua",
                python_exe: Optional[str] = None) -> subprocess.Popen:
    """启动 pip 安装子进程（流式输出由调用方读取）。"""
    cmd = pip_cmd(packages, mirror_key, python_exe)
    log.info("pip 安装: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)


def torch_cuda_command(cuda_tag: str = "cu128") -> str:
    """返回训练环境安装 torch+CUDA 的完整命令（供 GUI 展示/复制）。

    默认 cu128: 覆盖 Volta~Hopper 及 Blackwell(RTX 50 系, sm_120),
    cu121 不支持 Blackwell。"""
    return (f"pip install torch torchvision "
            f"--index-url https://download.pytorch.org/whl/{cuda_tag}")


def ensure_venv_for_training(gs_repo: Path) -> Optional[Path]:
    """为 3DGS 仓库创建独立虚拟环境（不安装依赖，仅建壳）。

    冻结模式下 sys.executable 是主程序 EXE, 不能拿来建 venv——
    复用 venv_interpreter() 的解析（py 启动器/uv 目录, 版本对齐优先）。"""
    venv_dir = Path(gs_repo) / ".venv"
    py = venv_dir / "Scripts" / "python.exe"
    if py.exists():
        return py
    interp = venv_interpreter()
    if interp is None:
        log.error("创建训练环境失败: 找不到可用的系统 Python（建议安装 3.12 x64）")
        return None
    try:
        subprocess.run(interp + ["-m", "venv", str(venv_dir)],
                       check=True, capture_output=True, timeout=300,
                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("创建训练环境失败: %s", exc)
        return None
    return py if py.exists() else None
