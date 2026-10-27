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
from app.dcc.rizomuv_bridge import RizomuvBridge
from app.dcc.modo_bridge import ModoBridge
from app.dcc.ue5_bridge import UE5Bridge
from app.models.project import ProjectState, Stage
from app.models.settings import PipelineSettings
from app.utils import deps, licensing
from app.utils.logger import get_logger
from app.utils.security import (PathSecurityError, ensure_output_dir,
                                normalize_input, resolve_executable,
                                scrubbed_env)

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
    stage_failed = Signal(str)          # 阶段名，这个阶段失败被跳过了
    stage_skipped = Signal(str)          # 阶段名，主动跳过的，不算失败
    progress = Signal(int, str)         # 总进度 0-100，带一句消息
    dcc = Signal(str, str)              # (key, state: ok|busy|error|off|skipped)
    log_line = Signal(str, str)         # (level, message)
    artifact = Signal(str)              # 产物路径列表
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
        # 探测只有一套逻辑，CLI 和 GUI 共用（B-04）
        # settings 里手填的路径对命令行同样生效，不然同一台机器能问出两个答案
        self.dcc_infos = dcc_infos or DccDetector(
            {key: getattr(settings, field, None)
             for key, _name, field in DCC_PATH_FIELDS}).detect_all()
        self.failed_stages: List[str] = []
        self._model_path: Optional[Path] = None
        self._repair_attempted = False

    # ------------------------------------------------------------------ #
    def request_stop(self) -> None:
        self.stop_event.set()

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise PipelineAborted("用户取消")

    # ------------------------------------------------------------------ #
    def execute(self, source: "Path | List[Path] | str", work_dir: Path,
                model: Optional["Path | str"] = None) -> bool:
        """source 兼容三种形态: 单路径 str/Path、多源列表（视频+图片混合）。

        model: 可选外部点云（3DGS PLY）。提供时跳过 SFM/TRAIN, 直接进入
        MESH —— 既服务"已有模型"场景, 也为不接受 Inria 许可的用户提供
        合规路径（不执行非商业许可组件）。
        """
        if isinstance(source, (str, Path)):
            sources: List[Path] = [Path(source)]
        else:
            sources = [Path(s) for s in source]
        # M-01：输入输出先规范化（解符号链接、查存在、查输出根边界）
        # 校验不过不往上抛，按 INGEST 失败那套记档：一条失败，其余标"未执行"
        # 返回 False 就够了，GUI 线程不能因为一个坏路径崩，命令行也是这个口径
        try:
            sources = [normalize_input(s) for s in sources]
            work_dir = ensure_output_dir(work_dir)
            self._model_path = normalize_input(model) if model else None
            if self._model_path and not self._model_path.is_file():
                raise PathSecurityError(f"--model 不是文件: {self._model_path}")
        except (PathSecurityError, OSError) as exc:
            return self._abort_bad_paths(source, work_dir, exc)
        self.state = ProjectState(
            source="; ".join(str(s) for s in sources),
            work_dir=str(work_dir), started_at=time.time())
        log.info("=== Universal Scene Forge 启动 ===")
        log.info("输入源 x%d: %s", len(sources),
                 ", ".join(s.name for s in sources))
        if self._model_path:
            log.info("外部点云注入: %s（跳过 SFM/训练）", self._model_path)

        stages = [
            (Stage.INGEST.value, lambda: self._ingest(sources, work_dir)),
            (Stage.SFM.value, lambda: self._sfm(work_dir)),
            (Stage.TRAIN.value, lambda: self._train(work_dir)),
            (Stage.MESH.value, lambda: self._mesh(work_dir)),
            (Stage.RIZOMUV.value, lambda: self._rizomuv_unwrap(work_dir)),
            (Stage.UE5_PREVIEW.value, lambda: self._ue5_preview(work_dir)),
            (Stage.DCC_POST.value, lambda: self._blender_post(work_dir)),
            (Stage.REPAIR.value, lambda: self._metashape_repair(work_dir)),
            (Stage.EXPORT.value, lambda: self._export(work_dir)),
        ]
        for idx, (name, fn) in enumerate(stages):
            self._check_stop()
            self._safe(name, fn)
            if idx == 0 and self.failed_stages:
                # 采集一挂，后面七段全废，因为它们都吃帧数据
                # 硬跑下去只会刷一串连锁失败，看着吓人又没用，不如早点停、记一笔（C-04）
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
        out_dir = getattr(self, "_export_out_dir", None)
        if ok and out_dir is not None:
            summary += f" → {out_dir}"
        try:
            self.state.save(work_dir / "project.json")
        except OSError:
            pass
        self.bus.finished.emit(ok, summary)
        return ok

    # ------------------------------------------------------------------ #
    def _abort_bad_paths(self, source, work_dir: Path,
                         exc: Exception) -> bool:
        """路径预检被拒 → 与 INGEST 失败同语义留档并返回 False(C-04)。

        安全校验本身不放宽: 越界/不存在的路径仍然拒绝, 只是把拒绝结果
        走与采集失败一致的阶段留档流程, 而不是把异常抛给调用方。
        """
        raw = (str(source) if isinstance(source, (str, Path))
               else "; ".join(str(s) for s in source))
        self.state = ProjectState(source=raw, work_dir=str(work_dir),
                                  started_at=time.time())
        log.error("[SECURITY] 输入/输出路径被拒绝: %s", exc)
        self.bus.log_line.emit("ERROR", f"[SECURITY] 路径被拒绝: {exc}")
        self.state.record(Stage.INGEST.value, False, 0.0, message=str(exc))
        self.failed_stages.append(Stage.INGEST.value)
        self.bus.stage_failed.emit(Stage.INGEST.value)
        for stage in Stage:
            if stage.name in ("INIT", "DONE", "FAILED", "INGEST"):
                continue
            self.state.record(stage.value, None, 0.0, skipped=True,
                              message="采集阶段失败, 未执行")
        try:
            if Path(work_dir).is_dir():
                self.state.save(Path(work_dir) / "project.json")
        except OSError:
            pass
        self.bus.finished.emit(False, f"完成(含跳过步骤): {Stage.INGEST.value}")
        return False

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
        frames = ing.filter_blurry(frames)   # 把失焦和运动模糊的帧挑出去
        if getattr(self.settings, "enable_subject_mask", False):
            from app.core.masking import SubjectMasker, rembg_available
            if rembg_available():
                masker = SubjectMasker(
                    log_cb=lambda m: self.bus.log_line.emit("INFO", f"mask| {m}"),
                    stop_event=self.stop_event)
                masks = masker.generate(
                    frames, work_dir / "masks",
                    progress_cb=lambda d, tot, m: self._report(
                        3 + 4 * d / max(tot, 1), m))
                if masks:
                    self.state.masks = [str(m) for m in masks]
            else:
                self.bus.log_line.emit(
                    "WARNING", "主体遮罩已启用但 rembg 未安装, 跳过 "
                               "(pip install \"rembg[cpu]\")")
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
        if self._model_path:
            raise StageSkipped(
                f"已通过外部点云跳过 SfM: {self._model_path.name}")
        # CUDA 这条路只认 NVIDIA，COLMAP 的 CUDA SIFT 就这限制
        # AMD、Intel、核显一律回 CPU，功能不缺，就是慢（BUG-011）
        use_gpu = self.settings.enable_cuda_accel
        if use_gpu:
            from app.utils.gpu import probe
            if not probe().is_nvidia:
                use_gpu = False
                self.bus.log_line.emit(
                    "WARNING", "COLMAP GPU 加速仅支持 NVIDIA 显卡; "
                               "当前环境回退 CPU SIFT (结果一致, 速度较慢)")
        runner = SfmRunner(
            colmap_exe=self.settings.colmap_exe,
            use_gpu=use_gpu,
            progress_cb=lambda d, t, m: self._report(10 + 10 * d / max(t, 1), m),
            stop_event=self.stop_event,
            tools_root=self.settings.tools_root)
        dataset = runner.build_dataset(
            [Path(f) for f in self.state.frames], work_dir)
        self.state.register(Stage.SFM.value, [dataset])
        return dataset

    @staticmethod
    def _check_gpu_busy() -> Optional[str]:
        """S-02: 训练前检测其他进程的 GPU 占用 (防止训练被抢速 25 倍)。"""
        try:
            import subprocess
            exe = resolve_executable("nvidia-smi")
            if not exe:
                return None
            out = subprocess.run(
                [exe, "--query-compute-apps=pid,used_memory",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
                env=scrubbed_env(),
                creationflags=subprocess.CREATE_NO_WINDOW)
            apps = [l.strip() for l in out.stdout.strip().splitlines()
                    if l.strip()]
            if apps:
                return "; ".join(apps)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None

    def _train(self, work_dir: Path) -> Path:
        if self._model_path:
            # 用户自带点云就跳过 gaussian-splatting（Inria 那套不能随便跑），直接登记
            self.state.register(Stage.TRAIN.value, [self._model_path])
            self.bus.log_line.emit(
                "INFO", f"使用外部点云, 跳过 3DGS 训练: {self._model_path.name}")
            log.info("[LICENSE] 外部点云路径不执行 gaussian-splatting: %s",
                     self._model_path)
            raise StageSkipped("使用 --model 注入点云, 未执行 3DGS 训练")
        # 引擎按 settings.trainer 选（0.9.6 加的抽象），选中的不可用就回内置
        # 内置那条分支里还有 B-01 的解释器守卫和 CUDA 加速，别绕过
        busy = self._check_gpu_busy()
        if busy:
            self.bus.log_line.emit(
                "WARNING", "检测到其他进程正在使用 GPU (" + busy
                + ") —— 训练速度会显著下降, 建议关闭相关程序")
        from app.core import trainers
        engine = trainers.create_engine(
            self.settings,
            progress_cb=lambda cur, total, m: self._report(
                20 + 35 * cur / max(total, 1), f"{m} {cur}/{total}"),
            stop_event=self.stop_event,
            log_cb=lambda l: self.bus.log_line.emit("INFO", f"trainer| {l}"))
        ply = engine.train(work_dir / "dataset", work_dir / "model",
                           self.settings.gs_iterations)
        self.state.register(Stage.TRAIN.value, [ply])

        # M1.5 高斯过滤（0.9.6）：有遮罩就拿去投影采样，把背景高斯剔掉
        # 网格只围着主体长，Q-01 那个主体缺失就是这么修的
        if getattr(self.state, "masks", None):
            try:
                from app.core.gaussian_filter import filter_gaussians_by_masks
                filtered = filter_gaussians_by_masks(
                    Path(ply), Path(ply).with_name("point_cloud_subject.ply"),
                    work_dir / "dataset", work_dir / "masks")
                if filtered:
                    ply = filtered
                    self.state.register(Stage.TRAIN.value, [ply])
                    self.bus.log_line.emit(
                        "INFO", "已应用主体高斯过滤 (训练后投影采样)")
            except Exception as exc:  # noqa: BLE001 过滤失败回退原点云
                self.bus.log_line.emit(
                    "WARNING", f"主体高斯过滤失败, 使用原始点云: {exc}")
        return ply

    def _mesh(self, work_dir: Path) -> List[Path]:
        ply = self.state.artifacts.get(Stage.TRAIN.value, [None])[0]
        if not ply:
            raise RuntimeError("无点云可重建（TRAIN 阶段未产出高斯模型）")
        # 默认 TSDF。泊松那条路遇到半透明或高光主体（说的就是黑鼠标）会当噪声扔掉
        # 最后只剩背景一层薄片；深度图融合能把表面接起来。相机或环境缺了就退回去
        if getattr(self.settings, "enable_tsdf", True):
            try:
                from app.core.tsdf import build_mesh_from_depth
                from app.utils import deps
                py = deps.training_python(self.settings)
                model_dir = Path(ply).parents[2]
                if py and (model_dir / "cameras.json").is_file():
                    self.bus.log_line.emit(
                        "INFO", "网格后端: TSDF（3DGS 深度融合）")
                    objs = build_mesh_from_depth(
                        Path(ply), model_dir, work_dir / "dataset",
                        work_dir / "mesh",
                        gs_repo=Path(self.settings.gs_repo),
                        gs_python=str(py),
                        lod_ratios=self.settings.lod_ratios,
                        max_faces=150_000,
                        timeout_s=min(self.settings.blender_timeout_s, 1800),
                        log_cb=lambda m: self.bus.log_line.emit(
                            "INFO", f"tsdf| {m}"),
                        stop_event=self.stop_event)
                    self.state.register(Stage.MESH.value, objs)
                    for f in objs:
                        self.bus.artifact.emit(str(f))
                    return objs
                log.info("TSDF 跳过: 无训练解释器或相机位姿, 使用泊松重建")
            except Exception as exc:  # noqa: BLE001 —— TSDF 失败不阻断
                self.bus.log_line.emit(
                    "WARNING", f"TSDF 网格不可用, 回退泊松重建: {exc}")
        # Q-02：高斯多的时候把泊松八叉树加深，每深一级面数差不多翻四倍
        # 但只允许加两级，再往上内存就顶不住（emm，两级是试出来的）
        depth = self.settings.poisson_depth
        try:
            from app.utils.ply_io import PlyData
            n_gauss = len(PlyData.read(str(ply))["vertex"].data)
            if n_gauss > 500_000:
                depth = min(depth + 2, 12)
            elif n_gauss > 150_000:
                depth = min(depth + 1, 12)
            if depth != self.settings.poisson_depth:
                self.bus.log_line.emit(
                    "INFO", f"泊松深度自适应: {self.settings.poisson_depth}"
                            f" → {depth} (高斯 {n_gauss:,})")
        except Exception:  # noqa: BLE001 —— 计数失败按原深度
            depth = self.settings.poisson_depth
        extractor = SplatMeshExtractor(
            poisson_depth=depth,
            opacity_threshold=self.settings.opacity_threshold,
            density_quantile=self.settings.density_quantile)
        objs = extractor.run(Path(ply), work_dir / "mesh",
                             lod_ratios=self.settings.lod_ratios,
                             dataset_dir=work_dir / "dataset",
                             model_dir=work_dir / "model")
        self.state.register(Stage.MESH.value, objs)
        for f in objs:
            self.bus.artifact.emit(str(f))
        return objs

    def _best_meshes(self) -> List[Path]:
        """可用网格回退链: 3DGS 泊松网格 → Metashape 摄影测量修复网格。

        泼溅网格重建失败（open3d 缺失/有效高斯过少）但 Metashape 修复成功时,
        修复网格同样是合法产物 —— 后处理/导出必须能落地, 不能因单一阶段
        失败而让整个项目交白卷(C-07)。
        """
        meshes = [Path(f) for f in self.state.artifacts.get(Stage.MESH.value, [])]
        if meshes:
            # 展过 UV 的优先用 RizomUV 那份，比 Blender 的智能 UV 强不少
            uv_meshes = [Path(f) for f in
                         self.state.artifacts.get(Stage.RIZOMUV.value, [])]
            if uv_meshes:
                return [Path(uv_meshes[0])]
            return meshes
        repaired = [Path(f) for f in self.state.artifacts.get(Stage.REPAIR.value, [])]
        if repaired:
            self.bus.log_line.emit(
                "WARNING", "3DGS 网格缺失, 后处理/导出回退使用 Metashape 摄影测量网格")
            log.warning("MESH 产物缺失, 回退使用 REPAIR 网格: %s", repaired[0])
        return repaired

    def _rizomuv_unwrap(self, work_dir: Path) -> Optional[List[Path]]:
        """RizomUV 自动展 UV（可选环节, 未装/未启用优雅跳过）。

        游戏资产 UV 是贴图质量的决定因素; RizomUV 的打包质量优于
        Blender 兜底智能UV。展完的网格注册为 RIZOMUV 产物,
        _best_meshes 会让后续阶段优先使用它。
        """
        if not self.settings.enable_rizomuv:
            self.bus.dcc.emit("rizomuv", "skipped")
            raise StageSkipped("未启用 RizomUV 自动展 UV")
        # 3.4 授权检测只做存在性判断：找不到就跳过，不去绕也不去模拟
        try:
            licensing.require_optional_license("rizomuv")
        except licensing.LicenseBlocked as exc:
            self.bus.dcc.emit("rizomuv", "off")
            raise StageSkipped(f"RizomUV 授权未检测到, 已跳过: {exc}")
        info = self.dcc_infos.get("rizomuv")
        if not info or not info.available:
            self.bus.dcc.emit("rizomuv", "off")
            raise StageSkipped(
                "RizomUV 未安装, 展 UV 由 Blender 智能UV 兜底 (不影响导出)")
        mesh_files = self._best_meshes()
        if not mesh_files:
            raise StageSkipped("无网格可展 UV")
        self.bus.dcc.emit("rizomuv", "busy")
        bridge = RizomuvBridge(
            info.exe, timeout_s=self.settings.rizomuv_timeout_s,
            log_cb=lambda l: self.bus.log_line.emit("INFO", f"rizomuv| {l}"),
            stop_event=self.stop_event)
        out = work_dir / "uv" / f"{mesh_files[0].stem}_uv.obj"
        bridge.unwrap(mesh_files[0], out)
        self.state.register(Stage.RIZOMUV.value, [out])
        self.bus.artifact.emit(str(out))
        self._report(68, "RizomUV 展 UV 完成")
        return [out]

    def _ue5_preview(self, work_dir: Path) -> Optional[list]:
        """重建中间结果推流 UE5（容错: 未装 UE5 走 skipped 留档, 不算成功）。"""
        if not self.settings.enable_ue5_preview:
            self.bus.dcc.emit("ue5", "skipped")
            raise StageSkipped("未启用 UE5 实时预览")
        try:
            licensing.require_optional_license("ue5")
        except licensing.LicenseBlocked as exc:
            self.bus.dcc.emit("ue5", "off")
            raise StageSkipped(f"UE5 授权未检测到, 已跳过: {exc}")
        info = self.dcc_infos.get("ue5")
        if not info or not info.available:
            self.bus.dcc.emit("ue5", "off")
            raise StageSkipped("UE5 未安装, 实时预览跳过（不影响导出）")
        uproject = self.settings.ue5_uproject or find_ue5_uproject(
            info.exe, [str(Path(self.settings.gs_repo).parent)])
        if not uproject:
            # 没 .uproject 就现场生成一个最小工程，带上 PythonScriptPlugin
            # 头一次打开 UE 要编译、跑着色器，慢是正常的，别当卡死
            from app.dcc.ue5_bridge import ensure_preview_uproject
            uproject = ensure_preview_uproject(info.exe, work_dir)
            if uproject:
                self.settings.ue5_uproject = uproject
                self.bus.log_line.emit(
                    "INFO", f"已自动生成 UE 预览工程: {uproject}")
        if not uproject:
            self.bus.dcc.emit("ue5", "off")
            raise StageSkipped("未找到 .uproject 工程, UE5 预览跳过")
        self.bus.dcc.emit("ue5", "busy")
        bridge = UE5Bridge(info.exe, uproject,
                           timeout_s=self.settings.ue5_timeout_s,
                           log_cb=lambda l: self.bus.log_line.emit("INFO", f"ue5| {l}"),
                           stop_event=self.stop_event)
        mesh_files = self._best_meshes()
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
        mesh_files = self._best_meshes()
        if not mesh_files:
            raise RuntimeError("无网格可后处理")
        out = work_dir / "dcc"
        files = bridge.postprocess_mesh(
            mesh_in=mesh_files[0], out_dir=out, base_name="usf_scene",
            formats=[f for f in self.settings.export_formats
                     if f in ("fbx", "obj", "glb", "gltf", "blend", "usd")],
            lod_ratios=self.settings.lod_ratios,
            bake_ao=self.settings.bake_ao)
        self.bus.dcc.emit("blender", "ok")
        self.state.register(Stage.DCC_POST.value, files)
        for f in files:
            self.bus.artifact.emit(str(f))
        return files

    def _metashape_repair(self, work_dir: Path) -> Optional[List[Path]]:
        self._repair_attempted = True   # 给 _export 兜底用，免得同一轮修复跑两遍
        if not self.settings.enable_metashape_repair:
            self.bus.dcc.emit("metashape", "skipped")
            raise StageSkipped("未启用 Metashape 洞穴修复")
        try:
            licensing.require_optional_license("metashape")
        except licensing.LicenseBlocked as exc:
            self.bus.dcc.emit("metashape", "off")
            raise StageSkipped(f"Metashape 授权未检测到, 已跳过: {exc}")
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
        mesh_files = self._best_meshes()

        # 最后一道兜底（C-08）：走到导出了还一个网格都没有
        # 而 Metashape 明明能用、只是没触发过修复，那就自动跑一次
        # 宁可多花十分钟，也别让人拿着空目录回去
        if not mesh_files and not getattr(self, "_repair_attempted", False):
            info = self.dcc_infos.get("metashape")
            licensed = licensing.detect_optional_license("metashape")[0]
            if info and info.available and licensed:
                self.bus.log_line.emit(
                    "WARNING", "尚无任何网格: 为保证产出模型, 自动执行 Metashape "
                               "摄影测量修复 (兜底) …")
                try:
                    self._metashape_repair(work_dir)
                except Exception as exc:  # noqa: BLE001
                    self.bus.log_line.emit("ERROR", f"兜底修复失败: {exc}")
                mesh_files = self._best_meshes()
            elif info and info.available and not licensed:
                self.bus.log_line.emit(
                    "WARNING", "Metashape 已安装但未检测到用户自备授权, "
                               "不执行兜底修复（不做许可证绕过）")

        if not mesh_files:
            raise RuntimeError(
                "无网格可导出（3DGS 网格、Metashape 修复网格均缺失; "
                "请检查训练/网格阶段日志, 或在依赖体检中补装 open3d）")

        # 导出位置（C-09）：用户设了就用，没设就放工作目录的 output/
        if self.settings.export_dir:
            out_dir = Path(self.settings.export_dir)
        else:
            out_dir = work_dir / "output"
        # 导出目录先过边界（M-02）：盘符根和系统目录直接拒，再试写一下
        try:
            out_dir = ensure_output_dir(out_dir)
        except PathSecurityError as exc:
            raise RuntimeError(f"导出目录不可用: {exc}") from exc
        self.bus.log_line.emit("INFO", f"导出位置: {out_dir}")
        self._export_out_dir = out_dir
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
        modob = None
        modo_info = self.dcc_infos.get("modo")
        if modo_info and modo_info.available:
            modob = ModoBridge(modo_info.exe, self.settings.modo_timeout_s,
                               stop_event=self.stop_event)
        ue5 = None
        u_info = self.dcc_infos.get("ue5")
        if u_info and u_info.available and self.settings.ue5_uproject:
            licensed, detail = licensing.detect_optional_license("ue5")
            if licensed:
                ue5 = UE5Bridge(u_info.exe, self.settings.ue5_uproject,
                                self.settings.ue5_timeout_s)
            else:
                self.bus.log_line.emit(
                    "WARNING", f"UE5 uasset 导出跳过（无自备授权）: {detail}")
        dispatcher = ExportDispatcher(blender_bridge=blender, ue5_bridge=ue5,
                                      max_bridge=maxb, maya_bridge=mayab,
                                      houdini_bridge=houdinib, c4d_bridge=c4db,
                                      modo_bridge=modob)
        # FBX 和 .blend 是硬要求，设置里漏勾、老配置里没有，都照样补上
        # 这两个以 Blender 导出的为准，FBX 还有 DCC 那条兜底链
        formats = list(dict.fromkeys([*self.settings.export_formats,
                                      "fbx", "blend"]))
        added = [f for f in ("fbx", "blend")
                 if f not in self.settings.export_formats]
        if added:
            self.bus.log_line.emit(
                "INFO", f"导出格式补齐必需项: {', '.join(added)}")
        results = dispatcher.export_all(
            mesh_files[0], out_dir, formats=formats,
            lod_ratios=self.settings.lod_ratios,
            bake_ao=self.settings.bake_ao,
            texture_size=getattr(self.settings, "texture_size", 4096))
        flat = [f for files in results.values() for f in files
                if Path(f).exists()]   # 只记真落盘的东西，嘴上说成功不算
        missing_required = [f for f in ("fbx", "blend")
                            if not results.get(f)]
        if missing_required:
            raise RuntimeError(
                "必需格式导出缺失: " + ", ".join(missing_required)
                + "（请确认 Blender 可用且未在导出阶段报错）")

        # 顺手把训练出的 splat PLY 拷一份到导出目录
        # SuperSplat、PlayCanvas、Luma 这些查看器直接能拖进去看
        try:
            splat_src = next((Path(f) for f in
                              self.state.artifacts.get(
                                  Stage.TRAIN.value, [])
                              if Path(f).exists()), None)
            if splat_src:
                import shutil as _sh
                splat_dst = Path(out_dir) / "usf_scene_3dgs.ply"
                _sh.copy2(splat_src, splat_dst)
                flat.append(splat_dst)
                self.bus.log_line.emit(
                    "INFO", "高斯泼溅场景已导出: usf_scene_3dgs.ply "
                            "(可用 SuperSplat/UE5 打开)")
        except Exception as exc:  # noqa: BLE001
            self.bus.log_line.emit("WARNING", f"splat PLY 导出跳过: {exc}")

        self.state.register(Stage.EXPORT.value, flat)
        for f in flat:
            self.bus.artifact.emit(str(f))
        self._report(100, f"导出完成: {len(flat)} 个文件 → {out_dir}")
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
        except StageSkipped as skip:   # 主动跳过（P4）：记一笔，但不进失败清单
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
