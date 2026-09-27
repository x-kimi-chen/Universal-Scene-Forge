"""FFmpeg 持久化与检测链路修复（2026-09-10, v0.9.3）单元测试。

用户报告: 依赖体检装完 FFmpeg 后流水线仍报「FFmpeg 未安装」。
三层根因 → 三层修复:
  F) ffmpeg 下载源只有 gyan.dev + GitHub 直连(国内不可达) → 加 gh-proxy/ghfast 镜像
  S) 冻结模式 settings.json 写 Program Files(普通权限不可写, 装完重启即丢)
     → 改存 ~/.universal_scene_forge/ 并做旧位置一次性迁移
  I) Ingester 只查 settings→PATH, 无 tools_root 便携版兜底(与 check_tools 不一致)
     → 三级查找对齐
运行: .venv\\Scripts\\python.exe tests\\unit_ffmpeg_persist.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures = []


def check(no, desc, ok, detail=""):
    print(f"[{no}] {desc}: {'OK' if ok else 'FAIL'}"
          + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"[{no}] {desc}")


# --------------------------------------------------------------- #
# F) FFmpeg 下载源含国内镜像
# --------------------------------------------------------------- #
from app.dcc import auto_installer as ai

ff_urls = ai.OFFICIAL_SOURCES["ffmpeg"]["urls"]
check("F1", "FFmpeg 源 ≥ 4 个（官方直连 + 镜像回退）", len(ff_urls) >= 4,
      str(len(ff_urls)))
check("F2", "含 gh-proxy 镜像（GitHub 转发）",
      any("gh-proxy.com" in u for u in ff_urls))
check("F3", "含 ghfast 镜像（GitHub 转发）",
      any("ghfast.top" in u for u in ff_urls))
check("F4", "镜像源指向同一 GitHub 发布文件（前缀转发, 文件路径一致）",
      all(u.endswith("GyanD/codexffmpeg/releases/download/7.1/"
                     "ffmpeg-7.1-essentials_build.zip")
          or "gyan.dev" in u for u in ff_urls))
check("F5", "官方源在前、镜像源在后（回退顺序）",
      ff_urls[0].startswith("https://www.gyan.dev")
      and "github.com" in ff_urls[1])

# --------------------------------------------------------------- #
# S) settings.json 冻结模式持久化 + 旧位置迁移
# --------------------------------------------------------------- #
from app.models import settings as st
from app.models.settings import PipelineSettings
from app.utils.paths import resource_root

check("S1", "源码模式 settings_file = 项目根 settings.json",
      st.settings_file() == resource_root() / "settings.json",
      str(st.settings_file()))

_frozen_had = hasattr(sys, "frozen")
try:
    sys.frozen = True
    check("S2", "冻结模式 settings_file 指向用户主目录（可写）",
          st.settings_file() == Path.home() / ".universal_scene_forge"
          / "settings.json", str(st.settings_file()))
finally:
    if _frozen_had:
        sys.frozen = True
    else:
        del sys.frozen

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "s.json"
    s = PipelineSettings(ffmpeg_exe="X:/tools/ffmpeg.exe", pip_mirror="aliyun")
    s.save(p)
    check("S3", "save() 自动建父目录并持久化", p.exists())
    s2 = PipelineSettings.load(p)
    check("S4", "load() roundtrip 读回字段",
          s2.ffmpeg_exe == "X:/tools/ffmpeg.exe" and s2.pip_mirror == "aliyun")
    check("S5", "load() 不存在的文件 → 默认值",
          PipelineSettings.load(Path(td) / "nope.json").pip_mirror == "tsinghua")
    (Path(td) / "bad.json").write_text("{not json", encoding="utf-8")
    check("S6", "损坏 json → 默认值不抛异常",
          PipelineSettings.load(Path(td) / "bad.json").frame_fps == 2.0)

# 迁移: 冻结模式 + 新位置缺失 + 旧位置(安装目录)存在 → 读入并写一份到新位置
with tempfile.TemporaryDirectory() as td:
    home = Path(td) / "home"
    home.mkdir()
    legacy_dir = Path(td) / "ProgramFiles"
    legacy_dir.mkdir()
    (legacy_dir / "settings.json").write_text(
        json.dumps({"ffmpeg_exe": "L:/ff/ffmpeg.exe", "colmap_exe": "L:/cm.bat"}),
        encoding="utf-8")
    orig_home = Path.home
    orig_legacy = st._legacy_settings_file
    _had = hasattr(sys, "frozen")
    try:
        sys.frozen = True
        Path.home = classmethod(lambda cls: home)
        st._legacy_settings_file = lambda: legacy_dir / "settings.json"
        got = PipelineSettings.load()
        new_file = home / ".universal_scene_forge" / "settings.json"
        check("S7", "迁移: 旧位置值被读入",
              got.ffmpeg_exe == "L:/ff/ffmpeg.exe" and got.colmap_exe == "L:/cm.bat",
              f"ffmpeg={got.ffmpeg_exe}")
        check("S8", "迁移: 已写入新的可写位置", new_file.exists())
        # 再 load: 优先新位置（旧位置不再是唯一来源）
        again = PipelineSettings.load()
        check("S9", "迁移后二次 load 读新位置（幂等）",
              again.ffmpeg_exe == "L:/ff/ffmpeg.exe")
    finally:
        Path.home = orig_home
        st._legacy_settings_file = orig_legacy
        if _had:
            sys.frozen = True
        else:
            del sys.frozen

# --------------------------------------------------------------- #
# I) Ingester 三级 ffmpeg 查找（与 deps.check_tools 对齐）
# --------------------------------------------------------------- #
import shutil as _shutil

from app.core.ingest import Ingester

_orig_which = _shutil.which
try:
    _shutil.which = lambda name: None  # 屏蔽开发机 PATH 上的 ffmpeg, 测兜底

    with tempfile.TemporaryDirectory() as td:
        tools = Path(td) / "tools"
        ff_dir = tools / "ffmpeg" / "ffmpeg-7.1-essentials_build" / "bin"
        ff_dir.mkdir(parents=True)
        ff_exe = ff_dir / "ffmpeg.exe"
        ff_exe.write_bytes(b"MZ")

        ing = Ingester(ffmpeg_exe="E:/explicit/ffmpeg.exe",
                       tools_root=str(tools))
        check("I1", "显式 ffmpeg_exe 优先", ing.ffmpeg == "E:/explicit/ffmpeg.exe")

        _shutil.which = lambda name: "P:/on/path/ffmpeg.exe"
        ing = Ingester(tools_root=str(tools))
        check("I2", "PATH 命中优先于 tools_root",
              ing.ffmpeg == "P:/on/path/ffmpeg.exe")

        _shutil.which = lambda name: None
        ing = Ingester(tools_root=str(tools))
        check("I3", "tools_root 便携版兜底（settings 丢失时仍可找到）",
              ing.ffmpeg == str(ff_exe), str(ing.ffmpeg))

        empty = Path(td) / "empty_tools"
        empty.mkdir()
        ing = Ingester(tools_root=str(empty))
        check("I4", "tools_root 无 ffmpeg → None（走 OpenCV 保底）",
              ing.ffmpeg is None)

        ing = Ingester()
        check("I5", "不传 tools_root 时不抛异常且为 None", ing.ffmpeg is None)
finally:
    _shutil.which = _orig_which

print()
if failures:
    print(f"*** {len(failures)} 项失败 ***")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("=== unit_ffmpeg_persist 全部通过 ===")
