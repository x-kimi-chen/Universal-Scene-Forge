"""项目状态机：阶段枚举、产物登记、断点档案。"""
from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


class Stage(enum.Enum):
    INIT = "初始化"
    INGEST = "输入采集与抽帧"
    SFM = "COLMAP 位姿求解"
    TRAIN = "3D 高斯泼溅训练"
    MESH = "泼溅网格重建"
    UE5_PREVIEW = "UE5 实时预览推送"
    DCC_POST = "Blender 网格后处理"
    REPAIR = "Metashape 洞穴修复"
    EXPORT = "多格式导出"
    DONE = "完成"
    FAILED = "失败"


@dataclass
class StageRecord:
    name: str
    ok: Optional[bool] = None
    skipped: bool = False
    message: str = ""
    elapsed_s: float = 0.0

    @property
    def status(self) -> str:
        """success | failed | skipped(P3)。

        ok=None 表示「未执行」（如采集失败后的级联留档、宿主未装的主动跳过）,
        与失败(ok=False)严格区分, 供 project.json 消费方直接判断。
        """
        if self.skipped:
            return "skipped"
        return "success" if self.ok else "failed"


@dataclass
class ProjectState:
    """一次重建任务的运行时状态（可序列化为 project.json 档案）。"""
    source: str = ""
    work_dir: str = ""
    frames: List[str] = field(default_factory=list)
    artifacts: Dict[str, List[str]] = field(default_factory=dict)  # stage -> files
    records: List[StageRecord] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    def record(self, name: str, ok: Optional[bool], elapsed: float,
               skipped: bool = False, message: str = "") -> None:
        self.records.append(StageRecord(name, ok, skipped, message, elapsed))

    def register(self, stage: str, files: List[Path]) -> None:
        bucket = self.artifacts.setdefault(stage, [])
        bucket.extend(str(f) for f in files)

    @property
    def all_artifacts(self) -> List[str]:
        return [f for files in self.artifacts.values() for f in files]

    def save(self, path: Path) -> None:
        self.finished_at = time.time()
        payload = {
            "source": self.source,
            "work_dir": self.work_dir,
            "frames": self.frames,
            "artifacts": self.artifacts,
            "records": [
                {"name": r.name, "ok": r.ok, "skipped": r.skipped,
                 "status": r.status,
                 "message": r.message, "elapsed_s": round(r.elapsed_s, 2)}
                for r in self.records
            ],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
