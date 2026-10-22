# -*- coding: utf-8 -*-
"""安全边界工具：路径规范化、输出根约束、可执行文件校验、子进程环境隔离。

设计目标（对应 SECURITY_AUDIT.md）:
- 所有外部输入/输出路径先 resolve 再做边界判定, 拒绝 NUL/空路径;
- 导出目录不允许落在系统敏感根（Windows/Program Files/用户主目录根）;
- 外部工具优先使用绝对路径; 裸命令名只允许 PATH 查找, 且拒绝解析到
  当前工作目录（防二进制投毒）;
- Python 子进程剥离 PYTHONPATH/PYTHONHOME/PYTHONSTARTUP, 并把 PATH 收缩
  到解释器目录 + System32, 防止 PATH 上的同名 DLL/exe 劫持。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

_WINDOWS = sys.platform == "win32"


class PathSecurityError(ValueError):
    """路径越界 / 不可用 / 命中敏感目录。"""


def _system_roots() -> list[Path]:
    """系统敏感目录（只拒绝“位于其中”的输出, 盘符根另行判定）。

    注意: 不要把盘符根（C:\\）放进这里 —— 否则整盘输出都会被拒绝;
    盘符根本身由 ensure_output_dir 的 p == p.anchor 分支单独拦截。
    """
    roots: list[Path] = []
    for key in ("SystemRoot", "windir"):
        v = os.environ.get(key)
        if v:
            roots.append(Path(v))
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(key)
        if v:
            roots.append(Path(v))
    return roots


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def normalize_input(path: "str | Path", *, must_exist: bool = True) -> Path:
    """规范化输入路径: 展开用户目录、拒绝 NUL、resolve（跟随符号链接）。"""
    raw = str(path)
    if not raw.strip():
        raise PathSecurityError("空路径")
    if "\x00" in raw:
        raise PathSecurityError("路径包含 NUL 字符")
    p = Path(raw).expanduser()
    if must_exist and not p.exists():
        raise PathSecurityError(f"路径不存在: {p}")
    return p.resolve(strict=must_exist)


def ensure_output_dir(path: "str | Path", *, create: bool = True,
                      forbidden_roots: Optional[Iterable[Path]] = None) -> Path:
    """校验并（可选）创建输出目录。

    拒绝:
    - 已存在但不是目录;
    - 位于系统目录 / Program Files / 用户主目录根 / 盘符根;
    - 创建后不可写。
    """
    raw = str(path)
    if not raw.strip():
        raise PathSecurityError("空输出路径")
    if "\x00" in raw:
        raise PathSecurityError("输出路径包含 NUL 字符")
    p = Path(raw).expanduser().resolve(strict=False)

    if p == p.anchor and p.anchor:
        raise PathSecurityError(f"拒绝把盘符根作为输出目录: {p}")

    roots = list(forbidden_roots) if forbidden_roots is not None else _system_roots()
    for root in roots:
        try:
            root = Path(root).resolve(strict=False)
        except OSError:
            continue
        if _is_within(p, root):
            raise PathSecurityError(f"输出目录位于系统敏感根 {root}: {p}")
    # 用户主目录/盘符根本身: 允许其子目录, 拒绝把整目录当导出根
    home = Path.home().resolve(strict=False)
    if p == home:
        raise PathSecurityError(f"拒绝把用户主目录作为输出根: {p}")

    if p.exists() and not p.is_dir():
        raise PathSecurityError(f"输出路径已存在且不是目录: {p}")
    if create and not p.exists():
        try:
            p.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            pass
    if p.exists():
        probe = p / ".usf_write_probe"
        try:
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            raise PathSecurityError(f"输出目录不可写: {p} ({exc})") from exc
    return p


def ensure_within(path: "str | Path", root: "str | Path") -> Path:
    """确认 path 在 root 之内（用于脚本/产物目录约束）。"""
    p = Path(path).expanduser().resolve(strict=False)
    r = Path(root).expanduser().resolve(strict=False)
    if not _is_within(p, r):
        raise PathSecurityError(f"路径越界: {p} 不在允许根 {r} 内")
    return p


def resolve_executable(candidate: Optional[str], *, allow_path_lookup: bool = True) -> Optional[str]:
    """解析外部工具路径。

    - 含目录分隔符: 必须是绝对路径且存在（拒绝相对路径）;
    - 裸命令名: 仅当 allow_path_lookup 时走 PATH, 并拒绝解析到 CWD
      （Windows 下当前目录优先于 PATH 的经典投毒面）。
    """
    if not candidate:
        return None
    raw = str(candidate).strip()
    if not raw or "\x00" in raw:
        return None
    p = Path(raw)
    if p.is_absolute() or p.parent != Path("."):
        if not p.is_absolute():
            return None
        return str(p.resolve()) if p.is_file() else None
    if not allow_path_lookup:
        return None
    found = shutil.which(raw)
    if not found:
        return None
    resolved = Path(found).resolve()
    try:
        resolved.relative_to(Path.cwd().resolve())
        return None          # PATH 解析结果落在 CWD: 拒绝
    except ValueError:
        pass
    return str(resolved)


def scrubbed_env(python_exe: Optional[str] = None,
                 extra: Optional[dict] = None) -> dict:
    """构造最小化环境变量。

    - 删除 PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/PYTHONUSERBASE;
    - 指定 python_exe 时 PATH 仅保留 解释器目录 + System32 + Windows;
    - extra 为增量覆盖（用于 CUDA_VISIBLE_DEVICES 等）。
    """
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("PYTHONPATH", "PYTHONHOME",
                                "PYTHONSTARTUP", "PYTHONUSERBASE")}
    if python_exe:
        py_dir = str(Path(python_exe).resolve().parent)
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        env["PATH"] = os.pathsep.join(
            [py_dir, str(Path(sysroot) / "System32"), sysroot])
    else:
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        env["PATH"] = os.pathsep.join(
            [str(Path(sysroot) / "System32"), sysroot])
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def system_tool(name: str) -> str:
    """定位系统自带工具（taskkill 等）的绝对路径, 避免 PATH 劫持。"""
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    cand = Path(sysroot) / "System32" / name
    return str(cand) if cand.is_file() else name


# 批处理/命令行元字符: 出现在插入 .bat 的路径中会改变命令语义
_BAT_META = set('&|<>^%!"\r\n')


def bat_quote(value: str) -> str:
    """把路径安全地放进 .bat 双引号参数; 含元字符直接拒绝。"""
    text = str(value)
    if any(ch in _BAT_META for ch in text):
        raise PathSecurityError(f"路径含批处理元字符, 拒绝拼接: {text}")
    return '"' + text + '"'
