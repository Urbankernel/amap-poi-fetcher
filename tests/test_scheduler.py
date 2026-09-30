"""Scheduler 集成测试（假 client，覆盖递归四分主流程与脏数据）。

场景：
1. 某网格 count=250 ≥ 阈值 → 四分裂，子网格正常拉取；
2. 相邻网格返回重复 poi_id → 全局只保留 1 条；
3. 触底仍超限 → 记 truncated，尽力拉取；
4. 单网格 API 错误 → 记 failed，不中断全局；
5. 全部 key 失效 → AllKeysExhausted，state 已保存可续跑。
"""

import csv

import pytest
from shapely.geometry import box

from amap_poi_fetcher.client import AmapApiError
from amap_poi_fetcher.coords import wgs84_to_gcj02
from amap_poi_fetcher.keys import AllKeysExhausted
from amap_poi_fetcher.scheduler import Scheduler, poi_to_row
from amap_poi_fetcher.store import RunStore


def make_poi(poi_id, lng=121.05, lat=31.05):
    return {
        "id": poi_id, "location": f"{lng},{lat}", "name": f"店{poi_id}",
        "type": "餐饮服务;快餐店;肯德基", "typecode": "050301",
        "cityname": "上海市", "adname": "浦东新区", "address": "世纪大道 1 号",
    }


def gpoly(xmin, ymin, xmax, ymax):
    """网格(WGS-84) → 请求坐标(GCJ-02)的 polygon 字符串，与 Grid.polygon_str 一致。"""
    nw_lng, nw_lat = wgs84_to_gcj02(xmin, ymax)
    se_lng, se_lat = wgs84_to_gcj02(xmax, ymin)
    return f"{nw_lng:.6f},{nw_lat:.6f}|{se_lng:.6f},{se_lat:.6f}"


class FakeClient:
    """按 polygon_str 前缀脚本化返回的假 client。"""

    def __init__(self, script):
        self.script = script  # polygon_str -> ("ok", count, pois) | ("error", msg) | ("exhausted",)
        self.pool = type("P", (), {"to_dict": lambda self: {}})()
        self.request_count = 0

    def polygon_first_page(self, polygon, types, *, offset, extensions):
        self.request_count += 1
        item = self.script.get(polygon, ("ok", 0, []))
        if item[0] == "error":
            raise AmapApiError(item[1])
        if item[0] == "exhausted":
            raise AllKeysExhausted("全部 key 不可用")
        _, count, pois = item
        return count, pois[:20]

    def polygon_iter(self, polygon, types, *, offset, extensions, count, first_pois):
        item = self.script.get(polygon, ("ok", 0, []))
        _, _, pois = item
        yield from pois


def make_scheduler(tmp_path, script, *, threshold=200, max_depth=4, min_size=0.005):
    store = RunStore.create(tmp_path / "runs", "test", {"types": "050301"})
    scheduler = Scheduler(
        FakeClient(script), store,
        types="050301", offset=20,
        split_threshold=threshold, max_depth=max_depth, min_size=min_size,
    )
    return scheduler, store


def read_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_recursive_split_and_dedup(tmp_path):
    geom = box(121.0, 31.0, 121.2, 31.2)
    # 2×2 渔网：g0_0 超限四分裂，其余正常；g0_0-0 与 g1_0 返回同一 poi（边界重复）
    grids = [
        ("g0_0", 121.0, 31.1, 121.1, 31.2),
        ("g1_0", 121.1, 31.1, 121.2, 31.2),
        ("g0_1", 121.0, 31.0, 121.1, 31.1),
        ("g1_1", 121.1, 31.0, 121.2, 31.1),
    ]
    script = {}
    for gid, x1, y1, x2, y2 in grids:
        poly = gpoly(x1, y1, x2, y2)
        if gid == "g0_0":
            script[poly] = ("ok", 250, [])  # 触发四分裂
        elif gid == "g0_1":
            script[poly] = ("ok", 1, [make_poi("DUP1")])
        elif gid == "g1_0":
            script[poly] = ("ok", 1, [make_poi("DUP1")])  # 重复 id
        else:
            script[poly] = ("ok", 0, [])
    # g0_0 的四个子网格（中心 121.05/31.15）
    sub = [
        (gpoly(121.0, 31.15, 121.05, 31.2), [make_poi("C1")]),
        (gpoly(121.05, 31.15, 121.1, 31.2), [make_poi("C2")]),
        (gpoly(121.0, 31.1, 121.05, 31.15), [make_poi("C3")]),
        (gpoly(121.05, 31.1, 121.1, 31.15), [make_poi("C4")]),
    ]
    for poly, pois in sub:
        script[poly] = ("ok", len(pois), pois)

    scheduler, store = make_scheduler(tmp_path, script)
    stats = scheduler.run(geom, 0.1)
    store.close()

    assert stats.grids_split == 1                     # 只有 g0_0 触发了四分裂
    assert stats.kept == 5                            # C1~C4 + DUP1（重复被去重）
    assert stats.fetched == 6                         # 拉取含 1 条重复
    rows = read_rows(store.csv_path)
    assert len(rows) == 5
    assert len({r["poi_id"] for r in rows}) == 5      # 磁盘无重复
    # 子网格 gid 溯源（渔网按 g{ix}_{iy} 编号，y 从南向北，北半格为 g0_1）
    assert {r["grid_id"] for r in rows if r["poi_id"].startswith("C")} == \
        {"g0_1-0", "g0_1-1", "g0_1-2", "g0_1-3"}
    # WGS-84 转换已生效且保留 GCJ-02 原值
    row = rows[0]
    assert abs(float(row["lon"]) - float(row["lon_gcj02"])) > 0


def test_truncated_grid_recorded(tmp_path):
    geom = box(121.0, 31.0, 121.01, 31.01)  # 单网格
    poly = gpoly(121.0, 31.0, 121.01, 31.01)
    script = {poly: ("ok", 300, [make_poi("T1", lng=121.005, lat=31.005)])}
    # max_depth=0 → 立即触底
    scheduler, store = make_scheduler(tmp_path, script, max_depth=0)
    stats = scheduler.run(geom, 0.1)
    store.close()

    assert stats.grids_truncated == 1
    assert stats.kept == 1  # 尽力拉取仍入库
    assert store.truncated_path.exists()
    assert "300" in store.truncated_path.read_text(encoding="utf-8")


def test_failed_grid_continues(tmp_path):
    geom = box(121.0, 31.0, 121.2, 31.1)  # 2 网格
    script = {
        gpoly(121.0, 31.0, 121.1, 31.1): ("error", "INVALID_PARAMS"),
        gpoly(121.1, 31.0, 121.2, 31.1): ("ok", 1, [make_poi("OK1")]),
    }
    scheduler, store = make_scheduler(tmp_path, script)
    stats = scheduler.run(geom, 0.1)
    store.close()

    assert stats.grids_failed == 1
    assert stats.kept == 1  # 第二个网格正常完成
    assert "INVALID_PARAMS" in store.failed_path.read_text(encoding="utf-8")


def test_all_keys_exhausted_saves_state(tmp_path):
    geom = box(121.0, 31.0, 121.2, 31.1)  # 2 网格
    script = {
        gpoly(121.0, 31.0, 121.1, 31.1): ("ok", 1, [make_poi("K1")]),
        gpoly(121.1, 31.0, 121.2, 31.1): ("exhausted",),
    }
    scheduler, store = make_scheduler(tmp_path, script)
    with pytest.raises(AllKeysExhausted):
        scheduler.run(geom, 0.1)
    store.close()

    state = store.load_state()
    assert len(state["completed"]) == 1       # 第一个网格已完成
    assert len(state["pending"]) == 1         # 第二个网格待续跑
    assert state["stats"]["kept"] == 1
    assert len(read_rows(store.csv_path)) == 1  # 已抓数据落盘未丢


def test_scope_filtering(tmp_path):
    """范围过滤：边界网格带回范围外的点应被丢弃，范围内的保留。"""
    # 范围只有 bbox 西半；网格（与 bbox 同大）模拟 API 返回了范围外东侧的点
    geom = box(121.0, 31.0, 121.1, 31.1)
    script = {
        gpoly(121.0, 31.0, 121.1, 31.1): ("ok", 2, [
            make_poi("IN", lng=121.05, lat=31.05),    # 范围内
            make_poi("OUT", lng=121.15, lat=31.05),   # 范围外（GCJ-02 转 WGS-84 后仍在界外）
        ]),
    }
    scheduler, store = make_scheduler(tmp_path, script)
    stats = scheduler.run(geom, 0.2)  # 单网格覆盖整个 bbox
    store.close()

    assert stats.fetched == 2
    assert stats.filtered == 1          # 范围外的点被过滤
    assert stats.kept == 1
    rows = read_rows(store.csv_path)
    assert [r["poi_id"] for r in rows] == ["IN"]


def test_scope_filtering_disabled(tmp_path):
    """filter_by_scope=False 时保留整个网格的数据。"""
    geom = box(121.0, 31.0, 121.1, 31.1)
    script = {
        gpoly(121.0, 31.0, 121.1, 31.1): ("ok", 1, [
            make_poi("OUT", lng=121.15, lat=31.05),
        ]),
    }
    store = RunStore.create(tmp_path / "runs", "test", {"types": "050301"})
    scheduler = Scheduler(
        FakeClient(script), store,
        types="050301", offset=20, filter_by_scope=False,
    )
    stats = scheduler.run(geom, 0.2)
    store.close()

    assert stats.filtered == 0
    assert stats.kept == 1


def test_poi_to_row_dirty_data():
    # 高德空字段返回 [] 的脏数据：应归一为空串而不是崩溃
    row = poi_to_row({"id": "X1", "location": "121.5,31.2", "name": "n",
                      "type": "t", "typecode": "050301", "cityname": [],
                      "adname": [], "address": []})
    assert row["address"] == "" and row["cityname"] == ""
    # 缺 id / 坐标坏 → None
    assert poi_to_row({"location": "121.5,31.2"}) is None
    assert poi_to_row({"id": "X2", "location": "bad"}) is None
    assert poi_to_row({"id": "X3", "location": ""}) is None
