"""主调度：递归四分防截断、分页拉取、去重落盘、断点续跑。

流程（单线程，逐网格深度优先）：
1. 范围几何 → 渔网（或从 state.json 恢复 pending 队列）
2. 每网格：第 1 页请求顺带拿 count
   - count ≥ split_threshold 且未触底 → 四分裂，子网格插队首继续
   - 触底仍超限 → 记 truncated_grids.jsonl + 警告，尽力分页拉取
   - 否则正常分页拉取
3. POI 逐条转换坐标（GCJ-02 → WGS-84）、poi_id 全局去重后批量 flush 落盘
4. 每网格完成即更新 state.json；AllKeysExhausted 时保存进度优雅退出
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from .client import AmapApiError, AmapClient
from .coords import gcj02_to_wgs84
from .dedup import PoiDedup
from .grid import Grid, fishnet, split
from .keys import AllKeysExhausted
from .store import RunStore

logger = logging.getLogger("amap_poi_fetcher.scheduler")


@dataclass
class Stats:
    """运行统计。fetched - filtered - kept = 网格边界重复被去重的条数。"""

    fetched: int = 0        # 拉取到的 POI 条数（含重复、含范围外）
    filtered: int = 0       # 范围过滤丢弃的条数（网格矩形覆盖范围外的点）
    kept: int = 0           # 过滤+去重后实际入库条数
    grids_done: int = 0     # 已处理网格（含细分叶网格）
    grids_split: int = 0    # 触发四分裂的网格数
    grids_failed: int = 0   # 失败网格数
    grids_truncated: int = 0  # 触底仍超限网格数

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Stats":
        return cls(**{k: int(v) for k, v in data.items() if k in cls.__dataclass_fields__})


def _as_str(value: Any) -> str:
    """字段转字符串。高德空字段会返回 [] 等非字符串，统一归一为空串。"""
    return value if isinstance(value, str) else ""


def poi_to_row(poi: dict[str, Any]) -> dict[str, Any] | None:
    """单条 API POI → 输出表行（含 GCJ-02→WGS-84 转换）。无 id/坐标返回 None。"""
    poi_id = _as_str(poi.get("id"))
    location = _as_str(poi.get("location"))
    if not poi_id or "," not in location:
        return None
    try:
        lon_gcj02, lat_gcj02 = (float(v) for v in location.split(",", 1))
    except ValueError:
        return None
    lon, lat = gcj02_to_wgs84(lon_gcj02, lat_gcj02)
    return {
        "poi_id": poi_id,
        "lon": round(lon, 7),
        "lat": round(lat, 7),
        "lon_gcj02": round(lon_gcj02, 7),
        "lat_gcj02": round(lat_gcj02, 7),
        "name": _as_str(poi.get("name")),
        "poi_type": _as_str(poi.get("type")),
        "poi_type_code": _as_str(poi.get("typecode")),
        "cityname": _as_str(poi.get("cityname")),
        "adname": _as_str(poi.get("adname")),
        "address": _as_str(poi.get("address")),
    }


class Scheduler:
    """递归四分调度器。

    参数:
        client: 高德 API 客户端
        store: 增量落盘/状态管理
        types: POI 类型编码（支持逗号或 | 分隔多类型）
        offset: 每页条数（≤25）
        extensions: all / base
        split_threshold: 单网格 POI 数达到此值即递归四分
        max_depth: 最大递归深度
        min_size: 最小网格边长（度），双保险
        filter_by_scope: 入库前按范围几何过滤 POI（网格是矩形，会覆盖到范围外）
    """

    def __init__(
        self,
        client: AmapClient,
        store: RunStore,
        *,
        types: str,
        offset: int = 20,
        extensions: str = "all",
        split_threshold: int = 200,
        max_depth: int = 4,
        min_size: float = 0.005,
        filter_by_scope: bool = True,
    ):
        self.client = client
        self.store = store
        self.types = types
        self.offset = offset
        self.extensions = extensions
        self.split_threshold = split_threshold
        self.max_depth = max_depth
        self.min_size = min_size
        self.filter_by_scope = filter_by_scope
        self.dedup = PoiDedup()
        self.stats = Stats()
        self.final_grids: list[Grid] = []      # 已完成的叶网格（可视化用）
        self.truncated_ids: set[str] = set()   # 触底超限网格 id（可视化标红用）
        self._scope = None                     # prepared 范围几何（run 时初始化）

    # ------------------------------------------------------------------ 主流程

    def run(self, geom: BaseGeometry, grid_size: float, state: dict[str, Any] | None = None) -> Stats:
        """执行抓取。state 非空时按断点续跑恢复进度。"""
        # prepared 几何加速逐点 covers 判断（万级点 + 复杂行政区边界时差距明显）
        self._scope = prep(geom) if self.filter_by_scope else None
        completed: set[str] = set()
        if state:
            queue = [Grid(**g) for g in state.get("pending", [])]
            completed = set(state.get("completed", []))
            self.stats = Stats.from_dict(state.get("stats", {}))
            self.dedup.load_csv(self.store.csv_path)
            self.client.request_count = int(state.get("request_count", 0))
            logger.info("断点续跑：pending %d 个网格，已完成 %d 个", len(queue), len(completed))
        else:
            queue = fishnet(geom, grid_size)
            logger.info("初始渔网：%d 个网格（size=%.4f°）", len(queue), grid_size)

        while queue:
            grid = queue.pop(0)
            if grid.gid in completed:
                continue
            try:
                self._fetch_grid(grid, queue)
            except AmapApiError as e:
                self.stats.grids_failed += 1
                self.store.mark_failed(grid, str(e))
                logger.error("网格 %s 失败（已记录，继续下一网格）: %s", grid.gid, e)
            except AllKeysExhausted as e:
                queue.insert(0, grid)  # 当前网格未完成，放回待处理队首
                self._save_progress(queue, completed)
                logger.error("%s。进度已保存，补充/更换 key 后用 --resume %s 续跑",
                             e, self.store.run_dir)
                raise
            self.stats.grids_done += 1
            completed.add(grid.gid)
            if self.stats.grids_done % 10 == 0:
                logger.info("进度：已处理 %d 网格，入库 %d 条（拉取 %d）",
                            self.stats.grids_done, self.stats.kept, self.stats.fetched)
            self._save_progress(queue, completed)

        self._save_progress([], completed)
        return self.stats

    # ------------------------------------------------------------------ 单网格

    def _fetch_grid(self, grid: Grid, queue: list[Grid]) -> None:
        """处理单个网格：探测 count → 递归四分或分页拉取。"""
        count, first = self.client.polygon_first_page(
            grid.polygon_str, self.types, offset=self.offset, extensions=self.extensions)
        logger.debug("网格 %s (depth=%d) count=%d", grid.gid, grid.depth, count)

        if count >= self.split_threshold:
            if grid.depth >= self.max_depth or grid.edge <= self.min_size:
                self.stats.grids_truncated += 1
                self.truncated_ids.add(grid.gid)
                self.store.mark_truncated(grid, count)
                logger.warning("网格 %s 触底仍超限（count=%d ≥ %d），尽力拉取，结果可能截断！"
                               "建议调小初始 grid.size 或拆分更细的 POI 类型后重跑该区域",
                               grid.gid, count, self.split_threshold)
                self._drain(grid, count, first)
            else:
                children = split(grid)
                self.stats.grids_split += 1
                logger.info("网格 %s count=%d ≥ %d，四分裂为 %d 个子网格（depth=%d）",
                            grid.gid, count, self.split_threshold, len(children), grid.depth + 1)
                queue[:0] = children  # 插队首，深度优先
            return
        self._drain(grid, count, first)

    def _drain(self, grid: Grid, count: int, first: list[dict[str, Any]]) -> None:
        """分页拉取一个网格的全部 POI，范围过滤 + 去重后落盘。"""
        batch: list[dict[str, Any]] = []
        for poi in self.client.polygon_iter(
                grid.polygon_str, self.types,
                offset=self.offset, extensions=self.extensions,
                count=count, first_pois=first):
            row = poi_to_row(poi)
            if row is None:
                continue
            self.stats.fetched += 1
            # 网格是矩形，边界网格会带回范围外的点 → 按范围几何二次过滤
            if self._scope is not None and \
                    not self._scope.covers(Point(row["lon"], row["lat"])):
                self.stats.filtered += 1
                continue
            if self.dedup.add(row["poi_id"]):
                row["grid_id"] = grid.gid
                batch.append(row)
                self.stats.kept += 1
        if batch:
            self.store.append_pois(batch)
        self.final_grids.append(grid)
        logger.debug("网格 %s 完成：入库 %d 条", grid.gid, len(batch))

    # ------------------------------------------------------------------ 进度

    def _save_progress(self, queue: list[Grid], completed: set[str]) -> None:
        self.store.save_state({
            "completed": sorted(completed),
            "pending": [asdict(g) for g in queue],
            "stats": self.stats.to_dict(),
            "key_pool": self.client.pool.to_dict(),
            "request_count": self.client.request_count,
        })
