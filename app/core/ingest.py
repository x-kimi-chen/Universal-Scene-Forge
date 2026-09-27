"""输入采集：多源输入（视频抽帧 / 图像序列收集 / 混合合并）/ CPU 并行预处理。"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, List, Optional

from app.utils.logger import get_logger

log = get_logger("INGEST")

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v",
              ".webm", ".flv", ".mpg", ".mpeg", ".ts", ".m2ts", ".mts"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

ProgressCb = Callable[[int, int, str], None]   # (done, total, msg)


class IngestError(RuntimeError):
    pass


class Ingester:
    def __init__(self, ffmpeg_exe: Optional[str] = None,
                 progress_cb: Optional[ProgressCb] = None,
                 stop_event: Optional[threading.Event] = None,
                 tools_root: Optional[str] = None):
        self.ffmpeg = ffmpeg_exe or shutil.which("ffmpeg")
        if not self.ffmpeg and tools_root:
            # 与 deps.check_tools 同款三级兜底: settings → PATH → tools_root
            # 便携版（settings 保存失败/丢失时仍能找到已装工具）
            from app.dcc.auto_installer import find_portable_exe
            exe = find_portable_exe("ffmpeg", Path(tools_root))
            self.ffmpeg = str(exe) if exe else None
        self.progress_cb = progress_cb
        self.stop_event = stop_event or threading.Event()

    # ------------------------------------------------------------------ #
    def collect(self, source: Path) -> List[Path]:
        """统一入口：视频 → 抽帧结果；图像目录/序列 → 排序后的帧列表。"""
        if source.is_file() and source.suffix.lower() in VIDEO_EXTS:
            raise IngestError(  # 视频需指定输出目录, 请调用 extract_frames()
                "视频输入请使用 extract_frames(video, out_dir)")
        if source.is_dir():
            frames = sorted(
                p for p in source.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
            if frames:
                return frames
            raise IngestError(f"目录中未找到图像: {source}")
        if source.is_file() and source.suffix.lower() in IMAGE_EXTS:
            return [source]
        raise IngestError(f"不支持的输入: {source}")

    # ------------------------------------------------------------------ #
    def collect_multi(self, sources: List[Path], out_dir: Path,
                      fps: float = 2.0, max_frames: int = 300) -> List[Path]:
        """多源统一入口: 视频/图像目录/单张图片混合, 逐源收集后合并去重。

        - 每个视频抽帧到 out_dir 下独立子目录（避免不同视频同名帧覆盖）
        - 单个源失败只跳过该源并记日志, 不阻断其余源
        - 全部源无产出时抛 IngestError
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        all_frames: List[Path] = []
        for i, src in enumerate(sources):
            src = Path(src)
            if not src.exists():
                log.warning("跳过不存在的输入源: %s", src)
                continue
            if src.is_file() and src.suffix.lower() in VIDEO_EXTS:
                sub = out_dir / f"{src.stem}_{i:02d}"
                try:
                    all_frames.extend(
                        self.extract_frames(src, sub, fps, max_frames))
                except IngestError as exc:
                    log.warning("视频 %s 抽帧失败, 已跳过: %s", src.name, exc)
                continue
            try:
                all_frames.extend(self.collect(src))
            except IngestError as exc:
                log.warning("输入源 %s 收集失败, 已跳过: %s", src, exc)
        seen: set = set()
        uniq: List[Path] = []
        for f in all_frames:
            key = str(f.resolve()) if f.exists() else str(f)
            if key not in seen:
                seen.add(key)
                uniq.append(f)
        if not uniq:
            raise IngestError("所有输入源均未产出图像帧")
        log.info("多源输入完成: %d 个源 → 合并 %d 帧", len(sources), len(uniq))
        return uniq

    # ------------------------------------------------------------------ #
    def extract_frames(self, video: Path, out_dir: Path, fps: float = 2.0,
                       max_frames: int = 300) -> List[Path]:
        """视频抽帧: FFmpeg 优先（多线程解码 + 进度回传）, 失败/缺失 → OpenCV 保底。

        max_frames <= 0 表示不限制, 抽取全部帧。

        精简版 ffmpeg 常见两类缺口, 均自动降级:
        - 不含 pipe 协议 (-progress 不可用) → 去 flag 重试
        - 不含 image2 muxer (无法写图片序列) → cv2.VideoCapture 逐帧解码
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        if self.ffmpeg:
            frames = self._extract_ffmpeg(video, out_dir, fps, max_frames)
            if frames:
                return frames
            log.warning("FFmpeg 未产出任何帧, 降级 OpenCV 抽帧")
        else:
            log.warning("FFmpeg 未安装, 使用 OpenCV 抽帧保底"
                        "（依赖体检中可一键安装完整版）")
        return self._extract_cv2(video, out_dir, fps, max_frames)

    # ------------------------------------------------------------------ #
    def _extract_ffmpeg(self, video: Path, out_dir: Path, fps: float,
                        max_frames: int) -> List[Path]:
        pattern = str(out_dir / "frame_%05d.jpg")
        core = ["-i", str(video), "-vf", f"fps={fps}"]
        if max_frames > 0:                      # 0 = 不限制, 不传 -frames:v
            core += ["-frames:v", str(max_frames)]
        core += ["-q:v", "2", pattern]
        log.info("抽帧: %s @ %sfps (上限 %s)", video.name, fps,
                 max_frames if max_frames > 0 else "不限")
        last_err = ""
        for extra in (["-progress", "pipe:1", "-nostats"], []):
            cmd = [self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                   *extra, *core]
            try:
                return self._run_ffmpeg(cmd, out_dir, max_frames,
                                        progress=bool(extra))
            except IngestError as exc:
                last_err = str(exc)
                if "progress" in last_err.lower():
                    log.warning("当前 ffmpeg 不支持 -progress pipe, 去掉进度回传重试")
                    continue
                log.warning("FFmpeg 抽帧失败: %s", last_err[:200])
                return []
        raise IngestError(last_err)

    def _run_ffmpeg(self, cmd: List[str], out_dir: Path, max_frames: int,
                    progress: bool) -> List[Path]:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        try:
            for line in proc.stdout or []:
                if self.stop_event.is_set():
                    proc.kill()
                    raise IngestError("用户取消")
                if progress and line.startswith("frame="):
                    try:
                        done = int(line.split("=")[1].strip())
                    except ValueError:
                        continue
                    self._report(done, max_frames, "抽帧中")
            try:
                code = proc.wait(timeout=600)
            except subprocess.TimeoutExpired:
                raise IngestError("FFmpeg 超时未退出 (10 分钟)")
            err = (proc.stderr.read() if proc.stderr else "")
            if code != 0:
                raise IngestError(f"FFmpeg 退出码 {code}: {err.strip()[:400]}")
        finally:
            if proc.poll() is None:
                proc.kill()
        frames = sorted(out_dir.glob("frame_*.jpg"))
        log.info("抽帧完成: %d 帧", len(frames))
        return frames

    # ------------------------------------------------------------------ #
    def _extract_cv2(self, video: Path, out_dir: Path, fps: float,
                     max_frames: int) -> List[Path]:
        """OpenCV 逐帧解码保底（无 ffmpeg / 精简 ffmpeg 缺 image2 muxer 时）。"""
        import cv2
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            raise IngestError(f"OpenCV 无法解码视频: {video}")
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(src_fps / max(fps, 0.01)))
        log.info("cv2 抽帧: %s (源 %.1ffps, 步长 %d, 上限 %s)",
                 video.name, src_fps, step,
                 max_frames if max_frames > 0 else "不限")
        frames: List[Path] = []
        idx = 0
        try:
            while True:
                if self.stop_event.is_set():
                    raise IngestError("用户取消")
                ok, frame = cap.read()
                if not ok:
                    break
                if idx % step == 0:
                    dst = out_dir / f"frame_{len(frames):05d}.jpg"
                    cv2.imwrite(str(dst), frame,
                                [cv2.IMWRITE_JPEG_QUALITY, 92])
                    frames.append(dst)
                    self._report(len(frames), max_frames, "抽帧中(cv2)")
                    if max_frames > 0 and len(frames) >= max_frames:
                        break
                idx += 1
        finally:
            cap.release()
        log.info("cv2 抽帧完成: %d 帧", len(frames))
        return frames

    # ------------------------------------------------------------------ #
    def preprocess(self, frames: List[Path], max_side: int = 1600,
                   workers: int = 8) -> List[Path]:
        """CPU 并行预处理：长边>max_side 的帧降采样（省 SfM/训练显存）。"""
        def _shrink(path: Path) -> Path:
            try:
                import cv2  # 延迟导入，缺库时仅跳过预处理
            except ImportError:
                return path
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is None:
                return path
            h, w = img.shape[:2]
            scale = max_side / max(h, w)
            if scale < 1.0:
                img = cv2.resize(img, (int(w * scale), int(h * scale)),
                                interpolation=cv2.INTER_AREA)
                cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            return path

        with ThreadPoolExecutor(max_workers=workers) as pool:
            done = 0
            for _ in pool.map(_shrink, frames):
                done += 1
                self._report(done, len(frames), "预处理")
        log.info("预处理完成: %d 帧 (长边上限 %dpx)", len(frames), max_side)
        return frames

    # ------------------------------------------------------------------ #
    def make_thumbnails(self, frames: List[Path], out_dir: Path,
                        count: int = 12, size: int = 200) -> List[Path]:
        """供 GUI 预览窗生成缩略图。"""
        out_dir.mkdir(parents=True, exist_ok=True)
        step = max(1, len(frames) // count)
        picks = frames[::step][:count]
        thumbs: List[Path] = []
        try:
            import cv2
        except ImportError:
            return thumbs
        for i, f in enumerate(picks):
            img = cv2.imread(str(f))
            if img is None:
                continue
            h, w = img.shape[:2]
            scale = size / max(h, w)
            thumb = cv2.resize(img, (int(w * scale), int(h * scale)))
            dst = out_dir / f"thumb_{i:02d}.jpg"
            cv2.imwrite(str(dst), thumb)
            thumbs.append(dst)
        return thumbs

    def _report(self, done: int, total: int, msg: str) -> None:
        if self.progress_cb:
            self.progress_cb(done, max(total, done), msg)
