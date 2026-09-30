"""跨网格全局去重（按 poi_id）。

- 进程内 set 判重，写盘前去重，保证磁盘任何时刻无重复；
- 断点续跑冷启动时从增量 CSV 读回 poi_id 列重建集合。
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger("amap_poi_fetcher.dedup")


class PoiDedup:
    """poi_id 判重集合。"""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def __len__(self) -> int:
        return len(self._seen)

    def __contains__(self, poi_id: str) -> bool:
        return poi_id in self._seen

    def add(self, poi_id: str) -> bool:
        """登记 poi_id；返回 True 表示首次出现（应保留），False 表示重复。"""
        if poi_id in self._seen:
            return False
        self._seen.add(poi_id)
        return True

    def load_csv(self, path: str | Path) -> int:
        """从既有增量 CSV 读回 poi_id 列重建集合，返回载入条数。"""
        path = Path(path)
        if not path.exists():
            return 0
        before = len(self._seen)
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                poi_id = (row.get("poi_id") or "").strip()
                if poi_id:
                    self._seen.add(poi_id)
        loaded = len(self._seen) - before
        logger.info("断点续跑：从 %s 重建去重集合，已有 %d 条", path.name, len(self._seen))
        return loaded
