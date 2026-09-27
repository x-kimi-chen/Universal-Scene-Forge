"""PipelineController — 流水线编排器（MVC 的 C）。

阶段: INGEST → SFM → TRAIN → MESH → UE5预览 → Blender后处理
      → Metashape修复(可选) → 导出

容错策略: 每个阶段包在 _safe() 中——异常 → 红灯 + ERROR 日志 → 跳过继续;
宿主未装/功能未启用抛 StageSkipped → 蓝灯 skipped 留档（非失败, 不影响退出码）。
与 GUI 解耦: 通过 EventBus(Qt Signal) 广播进度/状态, 控制器本身不碰控件。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, Signal

from app.core.colmap import SfmRunner
from app.core.exporter import ExportDispatcher
from app.core.gaussian import GaussianEngine
from app.core.ingest import Ingester, IngestError
from app.core.mesh import SplatMeshExtractor
from app.dcc.blender_bridge import BlenderBridge
from app.dcc.max_bridge import MaxBridge
from app.dcc.maya_bridge import MayaBridge
from app.dcc.metashape_bridge import MetashapeBridge
from app.dcc.houdini_bridge import HoudiniBridge
from app.dcc.c4d_bridge import C4dBridge
from app.dcc.registry_probe import DCC_PATH_FIELDS, DccDetector, find_ue5_uproject
from app.dcc.ue5_bridge import UE5Bridge
from app.models.project import ProjectState, Stage
from app.models.settings import PipelineSettings
from app.utils import deps
from app.utils.logger import get_logger

log = get_logger("PIPELINE")


class PipelineAborted(RuntimeError):
    pass


class StageSkipped(RuntimeError):
    """阶段主动跳过（宿主未安装 / 功能未启用 / 无输入）。

    与失败的区别(P4): 不计入 failed_stages, 总结不亮红,
    project.json 记 status=skipped（ok=null）而非伪成功。
    """


class EventBus(QObject):
    """控制器 → 视图 的唯一通信通道（跨线程自动队列）。"""
    stage = Signal(str)                 # 阶段名
    stage_failed = Signal(str)          # 阶段名（该阶段失败被跳过）
    stage_skipped = Signal(str)          # 阶段名（该阶段主动跳过, 非失败）
    progress = Signal(int, str)         # 总进度 0-100 + 消息
    dcc = Signal(str, str)              # (key, state: ok|busy|error|off|skipped)
    log_line = Signal(str, str)         # (level, message)
    artifact = Signal(str)              # 产物路径
    preview = Signal(str)               # 缩略图目录
    finished = Signal(bool, str)        # (成功?, 总结)


class PipelineController:
    def __init__(self, settings: PipelineSettings, bus: EventBus,
                 dcc_infos: Optional[dict] = None,
                 stop_event: Optional[threading.Event] = None):
        self.settings = settings
        self.bus = bus
        self.state = ProjectState()
        self.stop_event = stop_event or threading.Event()
        # CLI/GUI 共用一套三级探测(B-04): 显式路径设置(settings.<tool>_exe)
        # 必须同样对 CLI 生效, 否则同一台机器两边探测结果不一致。
        self.dcc_infos = dcc_infos or DccDetector(
            {key: getattr(settings, field, None)
             for key, _name, field in DCC_PATH_FIELDS}).detect_all()
        self.failed_stages: List[str] = []

    # ------------------------------------------------------------------ #
    def request_stop(self) -> None:
        self.stop_event.set()

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise PipelineAborted("用户取消")

    # ------------------------------------------------------------------ #
    def execute(self, source: "Path | List[Path] | str", work_dir: Path) -> bool:
        """source 兼容三种形态: 单路径 str/Path、多源列表（视频+图片混合）。"""
        if isinstance(source, (str, Path)):
            sources: List[Path] = [Path(source)]
        else:
            sources = [Path(s) for s in source]
        self.state = ProjectState(
            source="; ".join(str(s) for s in sources),
            work_dir=str(work_dir), started_at=time.time())
        log.info("=== Universal Scene Forge 启动 ===")
        log.info("输入源 x%d: %s", len(sources),
                 ", ".join(s.name for s in sources))
        work_dir.mkdir(parents=True, exist_ok=True)

        stages = [
            (Stage.INGEST.value, lambda: self._ingest(sources, work_dir)),
            (Stage.SFM.value, lambda: self._sfm(work_dir)),
            (Stage.TRAIN.value, lambda: self._train(work_dir)),
            (Stage.MESH.value, lambda: self._mesh(work_dir)),
            (Stage.UE5_PREVIEW.value, lambda: self._ue5_preview(work_dir)),
            (Stage.DCC_POST.value, lambda: self._blender_post(work_dir)),
            (Stage.REPAIR.value, lambda: self._metashape_repair(work_dir)),
            (Stage.EXPORT.value, lambda: self._export(work_dir)),
        ]
        for idx, (name, fn) in enumerate(stages):
            self._check_stop()
            self._safe(name, fn)
            if idx == 0 and self.failed_stages:
                # INGEST 失败: 后续 7 个阶段全部依赖帧数据, 级联执行只会产生
                # 一串误导性的连锁失败。提前终止并留档(C-04)。
                for skip_name, _fn in stages[idx + 1:]:
                    self.state.record(skip_name, None, 0.0, skipped=True,
                                      message="采集阶段失败, 未执行")
                self.bus.log_line.emit(
                    "ERROR", "采集阶段失败, 已终止后续阶段（无有效输入帧）")
                log.error("采集阶段失败, 终止后续阶段")
                break

        ok = not self.failed_stages
        summary = (f"完成。产物 {len(self.state.all_artifacts)} 个"
                   if ok else f"完成(含跳过步骤): {', '.join(self.failed_stages)}")
        try:
            self.state.save(work_dir / "project.json")
        except OSError:
            pass
        self.bus.finished.emit(ok, summary)
        return ok

    # ------------------------------------------------------------------ #
    #  各阶段实现
    # ------------------------------------------------------------------ #
    def _ingest(self, sources: List[Path], work_dir: Path) -> List[Path]:
        """多源采集: 视频逐个抽帧到 frames/<视频名>_NN 子目录, 图像直接收集, 合并去重。"""
        frames_dir = work_dir / "frames"
        ing = Ingester(ffmpeg_exe=self.settings.ffmpeg_exe,
                       progress_cb=lambda d, t, m: self._report(10 * d / max(t, 1), m),
                       stop_event=self.stop_event,
                       tools_root=self.settings.tools_root)
        frames = ing.collect_multi(
            sources, frames_dir, self.settings.frame_fps,
            self.settings.max_frames)
        frames = ing.preprocess(
            frames, self.settings.preprocess_max_side,
            self.settings.preprocess_workers)
        if not frames:
            raise IngestError("未获得任何有效帧")
        self.state.frames = [str(f) for f in frames]
        thumbs = ing.make_thumbnails(frames, work_dir / "thumbs")
        if thumbs:
            self.bus.preview.emit(str(work_dir / "thumbs"))
        self.state.register(Stage.INGEST.value, frames)
        return frames

    def _sfm(self, work_dir: Path) -> Path:
        runner = SfmRunner(
            colmap_exe=self.settings.colmap_exe,
            progress_cb=lambda d, t, m: self._report(10 + 10 * d / max(t, 1), m),
            stop_event=self.stop_event,
            tools_root=self.settings.tools_root)
        dataset = runner.build_dataset(
            [Path(f) for f in self.state.frames], work_dir)
        self.state.register(Stage.SFM.value, [dataset])
        return dataset

    def _train(self, work_dir: Path) -> Path:
        # 训练解释器: 显式 gs_python → 仓库内 .venv(B-01, 冻结模式下绝不回退
        # 到 sys.executable —— 那是主程序 EXE, 会吞掉 train.py 的参数)
        python_exe = deps.training_python(self.settings)
        engine = GaussianEngine(
            gs_repo=Path(self.settings.gs_repo),
            python_exe=str(python_exe) if python_exe else None,
            progress_cb=lambda cur, total, m: self._report(
                20 + 35 * cur / max(total, 1), f"{m} {cur}/{total}"),
            stop_event=self.stop_event)
        ply = engine.train(work_dir / "dataset", work_dir / "model",
                            self.settings.gs_iterations)
        self.state.register(Stage.TRAIN.value, [ply])
        return ply

    def _mesh(self, work_dir: Path) -> List[Path]:
        ply = self.state.artifacts.get(Stage.TRAIN.value, [None])[0]
        if not ply:
            raise RuntimeError("无点云可重建（TRAIN 阶段未产出高斯模型）")
        extractor = SplatMeshExtractor(
            poisson_depth=self.settings.poisson_depth,
            opacity_threshold=self.settings.opacity_threshold,
            density_quantile=self.settings.density_quantile)
        objs = extractor.run(Path(ply), work_dir / "mesh",
                             lod_ratios=self.settings.lod_ratios)
        self.state.register(Stage.MESH.value, objs)
        for f in objs:
            self.bus.artifact.emit(str(f))
        return objs

    def _ue5_preview(self, work_dir: Path) -> Optional[list]:
        """重建中间结果推流 UE5（容错: 未装 UE5 走 skipped 留档, 不算成功）。"""
        if not self.settings.enable_ue5_preview:
            self.bus.dcc.emit("ue5", "skipped")
            raise StageSkipped("未启用 UE5 实时预览")
        info = self.dcc_infos.get("ue5")
        if not info or not info.available:
            self.bus.dcc.emit("ue5", "off")
            raise StageSkipped("UE5 未安装, 实时预览跳过（不影响导出）")
        uproject = self.settings.ue5_uproject or find_ue5_uproject(
            info.exe, [str(Path(self.settings.gs_repo).parent)])
        if not uproject:
            self.bus.dcc.emit("ue5", "off")
            raise StageSkipped("未找到 .uproject 工程, UE5 预览跳过")
        self.bus.dcc.emit("ue5", "busy")
        bridge = UE5Bridge(info.exe, uproject,
                           timeout_s=self.settings.ue5_timeout_s,
                           log_cb=lambda l: self.bus.log_line.emit("INFO", f"ue5| {l}"),
                           stop_event=self.stop_event)
        mesh_files = [Path(f) for f in self.state.artifacts.get(Stage.MESH.value, [])]
        if not mesh_files:
            self.bus.dcc.emit("ue5", "skipped")
            raise StageSkipped("无网格产物可推送")
        imported = bridge.push_intermediate(mesh_files[0])
        self.bus.dcc.emit("ue5", "ok" if imported is not None else "error")
        self._report(68, "UE5 预览已推送")
        return imported

    def _blender_post(self, work_dir: Path) -> List[Path]:
        info = self.dcc_infos.get("blender")
        if not info or not info.available:
            self.bus.dcc.emit("blender", "off")
            raise RuntimeError("Blender 未安装（可在依赖体检中一键安装便携版）")
        self.bus.dcc.emit("blender", "busy")
        bridge = BlenderBridge(
            info.exe, timeout_s=self.settings.blender_timeout_s,
            progress_cb=lambda p, m: self._report(70 + 0.15 * p, f"Blender: {m}"),
            log_cb=lambda l: self.bus.log_line.emit("INFO", f"blender| {l}"),
            stop_event=self.stop_event)
        mesh_files = [Path(f) for f in self.state.artifacts.get(Stage.MESH.value, [])]
        if not mesh_files:
            raise RuntimeError("无网格可后处理")
        out = work_dir / "dcc"
        files = bridge.postprocess_mesh(
            mesh_in=mesh_files[0], out_dir=out, base_name="usf_scene",
            formats=[f for f in self.settings.export_formats
                     if f in ("fbx", "glb", "gltf", "blend")],
            lod_ratios=self.settings.lod_ratios,
            bake_ao=self.settings.bake_ao)
        self.bus.dcc.emit("blender", "ok")
        self.state.register(Stage.DCC_POST.value, files)
        for f in files:
            self.bus.artifact.emit(str(f))
        return files

    def _metashape_repair(self, work_dir: Path) -> Optional[List[Path]]:
        if not self.settings.enable_metashape_repair:
            self.bus.dcc.emit("metashape", "skipped")
            raise StageSkipped("未启用 Metashape 洞穴修复")
        info = self.dcc_infos.get("metashape")
        if not info or not info.available:
            self.bus.dcc.emit("metashape", "off")
            raise StageSkipped("Metashape 未安装, 洞穴修复跳过")
        self.bus.dcc.emit("metashape", "busy")
        bridge = MetashapeBridge(
            info.exe, timeout_s=self.settings.metashape_timeout_s,
            progress_cb=lambda p, m: self._report(85 + 0.05 * p, f"Metashape: {m}"),
            log_cb=lambda l: self.bus.log_line.emit("INFO", f"metashape| {l}"),
            stop_event=self.stop_event)
        repaired = work_dir / "dcc" / "usf_scene_repaired.obj"
        bridge.repair_mesh(Path(self.state.frames[0]).parent, repaired)
        self.bus.dcc.emit("metashape", "ok")
        self.state.register(Stage.REPAIR.value, [repaired])
        self.bus.artifact.emit(str(repaired))
        return [repaired]

    def _export(self, work_dir: Path) -> dict:
        mesh_files = [Path(f) for f in self.state.artifacts.get(Stage.MESH.value, [])]
        if not mesh_files:
            raise RuntimeError("无网格可导出")
        blender = None
        bl_info = self.dcc_infos.get("blender")
        if bl_info and bl_info.available:
            blender = BlenderBridge(bl_info.exe, self.settings.blender_timeout_s,
                                    stop_event=self.stop_event)
        maxb = None
        max_info = self.dcc_infos.get("max")
        if max_info and max_info.available:
            maxb = MaxBridge(max_info.exe, self.settings.max_timeout_s,
                             stop_event=self.stop_event)
        mayab = None
        maya_info = self.dcc_infos.get("maya")
        if maya_info and maya_info.available:
            mayab = MayaBridge(maya_info.exe, self.settings.maya_timeout_s,
                               stop_event=self.stop_event)
        houdinib = None
        hou_info = self.dcc_infos.get("houdini")
        if hou_info and hou_info.available:
            houdinib = HoudiniBridge(hou_info.exe, self.settings.houdini_timeout_s,
                                     stop_event=self.stop_event)
        c4db = None
        c4d_info = self.dcc_infos.get("c4d")
        if c4d_info and c4d_info.available:
            c4db = C4dBridge(c4d_info.exe, self.settings.c4d_timeout_s,
                             stop_event=self.stop_event)
        ue5 = None
        u_info = self.dcc_infos.get("ue5")
        if u_info and u_info.available and self.settings.ue5_uproject:
            ue5 = UE5Bridge(u_info.exe, self.settings.ue5_uproject,
                            self.settings.ue5_timeout_s)
        dispatcher = ExportDispatcher(blender_bridge=blender, ue5_bridge=ue5,
                                      max_bridge=maxb, maya_bridge=mayab,
                                      houdini_bridge=houdinib, c4d_bridge=c4db)
        results = dispatcher.export_all(
            mesh_files[0], work_dir / "output",
            formats=self.settings.export_formats,
            lod_ratios=self.settings.lod_ratios,
            bake_ao=self.settings.bake_ao)
        flat = [f for files in results.values() for f in files]
        self.state.register(Stage.EXPORT.value, flat)
        for f in flat:
            self.bus.artifact.emit(str(f))
        self._report(100, f"导出完成: {len(flat)} 个文件")
        return results

    # ------------------------------------------------------------------ #
    #  容错框架
    # ------------------------------------------------------------------ #
    def _safe(self, stage_name: str, fn) -> None:
        self.bus.stage.emit(stage_name)
        log.info("──── 阶段: %s ────", stage_name)
        t0 = time.time()
        try:
            fn()
            self.state.record(stage_name, True, time.time() - t0)
            log.info("[OK] %s 完成 (%.1fs)", stage_name, time.time() - t0)
        except PipelineAborted:
            self.state.record(stage_name, False, time.time() - t0,
                              message="用户取消")
            self.bus.finished.emit(False, "已取消")
            raise
        except StageSkipped as skip:   # 主动跳过(P4): 留档但不计失败
            self.state.record(stage_name, None, time.time() - t0,
                              skipped=True, message=str(skip))
            log.info("[SKIP] %s 跳过: %s", stage_name, skip)
            self.bus.stage_skipped.emit(stage_name)
        except Exception as exc:  # noqa: BLE001 —— 容错核心: 跳过并继续
            elapsed = time.time() - t0
            self.state.record(stage_name, False, elapsed, message=str(exc))
            self.failed_stages.append(stage_name)
            log.error("[X] %s 失败 (%.1fs): %s — 已跳过, 继续后续阶段",
                      stage_name, elapsed, exc)
            self.bus.stage_failed.emit(stage_name)
            self.bus.log_line.emit("ERROR", f"[{stage_name}] 失败: {exc}")

    def _report(self, percent: float, msg: str) -> None:
        self.bus.progress.emit(int(max(0, min(100, percent))), msg)
