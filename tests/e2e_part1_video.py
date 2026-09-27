"""E2E Part1: 合成视频 → 完整流水线 → 验证容错降级。

模拟真实环境: COLMAP 未安装 / 3DGS 仓库未克隆 / UE5 未安装 / Metashape 未装。
预期行为:
    - INGEST 真实成功 (ffmpeg 抽帧 + cv2 预处理 + 缩略图)
    - SFM / TRAIN / MESH / DCC_POST / EXPORT 失败但被 _safe() 捕获, 流水线不崩溃
    - UE5 / REPAIR 走灰灯跳过路径
    - project.json 状态档案正常落盘
判定: 全部断言通过 → 退出码 0。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402


def make_synthetic_video(path: Path, n: int = 24, w: int = 640, h: int = 480,
                         fps: int = 8) -> None:
    """生成双运动目标的合成视频（供 ffmpeg 真实抽帧）。"""
    import cv2
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert vw.isOpened(), "cv2.VideoWriter 打开失败"
    for i in range(n):
        frame = np.zeros((h, w, 3), np.uint8)
        frame[:, :, 0] = int(255 * i / n)                    # 时变渐变背景
        x = int((i / n) * (w - 120))                          # 目标1: 水平移动
        frame[h // 3:h // 3 + 100, x:x + 100] = (60, 200, 60)
        y = int((1 - i / n) * (h - 80))                       # 目标2: 垂直移动
        frame[y:y + 80, w // 2:w // 2 + 80] = (200, 60, 60)
        vw.write(frame)
    vw.release()


def main() -> int:
    from app.utils.logger import setup_logging
    from app.controllers.pipeline_controller import EventBus, PipelineController
    from app.models.settings import PipelineSettings

    setup_logging()
    work = Path(tempfile.mkdtemp(prefix="usf_e2e1_"))
    video = work / "synthetic.mp4"
    make_synthetic_video(video)
    print(f"[E2E1] 合成视频: {video} ({video.stat().st_size} bytes)")

    settings = PipelineSettings()
    bus = EventBus()
    ctrl = PipelineController(settings, bus)
    ok = ctrl.execute(video, work / "run")

    state = ctrl.state
    by_name = {r.name: r for r in state.records}

    checks = []

    def check(cond: bool, msg: str):
        checks.append((bool(cond), msg))
        print(f"  {'PASS' if cond else 'FAIL'}  {msg}")

    print("\n[E2E1] 阶段结果:")
    for r in state.records:
        status = "OK" if r.ok else ("SKIP" if r.skipped else "FAIL")
        print(f"  {r.name:12s} {status:4s} {r.elapsed_s:6.1f}s {r.message[:80]}")

    print("\n[E2E1] 断言:")
    check(video.exists() and video.stat().st_size > 10_000, "合成视频生成成功")
    check(by_name.get("输入采集与抽帧") is not None
          and by_name["输入采集与抽帧"].ok, "INGEST 真实成功 (ffmpeg 抽帧)")
    check(len(state.frames) >= 4, f"抽帧数量 {len(state.frames)} >= 4")
    check(all(Path(f).exists() for f in state.frames), "帧文件真实存在")
    check((work / "run" / "thumbs").exists()
          and any((work / "run" / "thumbs").glob("thumb_*.jpg")),
          "缩略图已生成")
    print("  ---- COLMAP 未安装 → SFM 失败属预期 ----")
    check(by_name.get("COLMAP 位姿求解") is not None
          and not by_name["COLMAP 位姿求解"].ok,
          "SFM 失败被容错捕获 (COLMAP 缺席)")
    check("COLMAP 位姿求解" in ctrl.failed_stages, "SFM 计入 failed_stages")
    check(by_name.get("3D 高斯泼溅训练") is not None
          and not by_name["3D 高斯泼溅训练"].ok,
          "TRAIN 失败被容错捕获 (仓库未克隆)")
    check(not ok, "整体返回 False (含失败阶段)")
    check((work / "run" / "project.json").exists(), "project.json 状态档案落盘")

    payload = json.loads((work / "run" / "project.json").read_text(encoding="utf-8"))
    check("frames" in payload and "records" in payload, "档案结构完整")

    n_pass = sum(1 for c, _ in checks if c)
    print(f"\n[E2E1] 结果: {n_pass}/{len(checks)} 断言通过 | 工作目录: {work}")
    return 0 if n_pass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
