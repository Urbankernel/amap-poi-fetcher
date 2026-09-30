"""增量落盘 + 断点续跑 + 失败/截断网格记录。

run 目录结构（每次运行独立子目录）：
    runs/<时间戳>_<标签>/
    ├── config.snapshot.yaml   配置快照（key 脱敏，续跑校验用）
    ├── pois_incremental.csv   增量落盘（每网格完成即 flush）
    ├── state.json             调度状态（completed/pending/stats/key 状态）
    ├── failed_grids.jsonl     失败网格（含原因，可复跑）
    ├── truncated_grids.jsonl  触底仍超限网格（疑似截断）
    ├── run.log                日志（由 log.attach_file_handler 写入）
    └── outputs/               完成后生成 pois.csv / pois.geojson / map.html
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from .grid import Grid

logger = logging.getLogger("amap_poi_fetcher.store")

FIELDNAMES = [
    "poi_id", "lon", "lat", "lon_gcj02", "lat_gcj02",
    "name", "poi_type", "poi_type_code", "cityname", "adname", "address",
    "grid_id",
]

STATE_VERSION = 1


def sanitize_tag(text: str, max_len: int = 30) -> str:
    """把范围名称等转成安全目录名片段。"""
    tag = re.sub(r"[^\w一-鿿-]+", "_", str(text)).strip("_")
    return tag[:max_len] or "run"


class RunStore:
    """单次运行的磁盘状态管理。"""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.outputs_dir = self.run_dir / "outputs"
        self.csv_path = self.run_dir / "pois_incremental.csv"
        self.state_path = self.run_dir / "state.json"
        self.failed_path = self.run_dir / "failed_grids.jsonl"
        self.truncated_path = self.run_dir / "truncated_grids.jsonl"
        self.snapshot_path = self.run_dir / "config.snapshot.yaml"
        self._csv_file = None
        self._writer: csv.DictWriter | None = None

    # ------------------------------------------------------------------ 创建/续跑

    @classmethod
    def create(cls, root: str | Path, tag: str, config_snapshot: dict[str, Any]) -> "RunStore":
        """创建新 run 目录并写入配置快照。同一秒内重名自动追加序号。"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = Path(root) / f"{ts}_{sanitize_tag(tag)}"
        run_dir, n = base, 1
        while run_dir.exists():
            n += 1
            run_dir = Path(f"{base}_{n}")
        (run_dir / "outputs").mkdir(parents=True)
        store = cls(run_dir)
        with open(store.snapshot_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config_snapshot, f, allow_unicode=True, sort_keys=False)
        store._open_csv(new=True)
        logger.info("run 目录: %s", run_dir)
        return store

    @classmethod
    def resume(cls, run_dir: str | Path) -> "RunStore":
        """打开既有 run 目录准备续跑。"""
        store = cls(run_dir)
        if not store.state_path.exists():
            raise FileNotFoundError(f"续跑失败：{store.state_path} 不存在，该目录不是有效的 run 目录")
        store._open_csv(new=False)
        logger.info("续跑 run 目录: %s", store.run_dir)
        return store

    def load_snapshot(self) -> dict[str, Any]:
        with open(self.snapshot_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # ------------------------------------------------------------------ 增量 CSV

    def _open_csv(self, new: bool) -> None:
        if new:
            self._csv_file = open(self.csv_path, "w", newline="", encoding="utf-8-sig")
        else:
            self._csv_file = open(self.csv_path, "a", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._csv_file, fieldnames=FIELDNAMES)
        if new:
            self._writer.writeheader()
            self._csv_file.flush()

    def append_pois(self, rows: Iterable[dict[str, Any]]) -> int:
        """追加一批 POI 行并立即 flush（进程被 kill 只丢当前网格）。"""
        assert self._writer is not None
        n = 0
        for row in rows:
            self._writer.writerow(row)
            n += 1
        if n:
            self._csv_file.flush()
        return n

    # ------------------------------------------------------------------ 状态

    def save_state(self, state: dict[str, Any]) -> None:
        """写 state.json（先写临时文件再替换，避免中断写出半个文件）。"""
        state = {"version": STATE_VERSION, **state}
        tmp = self.state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        tmp.replace(self.state_path)

    def load_state(self) -> dict[str, Any]:
        with open(self.state_path, encoding="utf-8") as f:
            state = json.load(f)
        if state.get("version") != STATE_VERSION:
            logger.warning("state.json 版本不匹配（%s），按兼容模式继续", state.get("version"))
        return state

    # ------------------------------------------------------------------ 失败/截断记录

    def _append_jsonl(self, path: Path, record: dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def mark_failed(self, grid: Grid, reason: str) -> None:
        self._append_jsonl(self.failed_path, {
            "grid": asdict(grid),
            "polygon": grid.polygon_str,
            "reason": reason,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })

    def mark_truncated(self, grid: Grid, count: int) -> None:
        self._append_jsonl(self.truncated_path, {
            "grid": asdict(grid),
            "polygon": grid.polygon_str,
            "count": count,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })

    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._csv_file is not None:
            self._csv_file.close()
            self._csv_file = None
            self._writer = None

    def __enter__(self) -> "RunStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
