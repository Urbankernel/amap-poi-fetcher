"""渔网与四分细分测试：数量、无缝覆盖、相交裁剪、四分裂。"""

import re

import pytest
from shapely.geometry import box
from shapely.ops import unary_union

from amap_poi_fetcher.grid import Grid, fishnet, split


def test_fishnet_count_and_coverage():
    geom = box(100.0, 30.0, 100.3, 30.2)
    grids = fishnet(geom, 0.1)
    assert len(grids) == 3 * 2  # 3 列 × 2 行
    # 无缝无重叠全覆盖（浮点容差）
    union = unary_union([g.to_polygon() for g in grids])
    assert abs(union.area - geom.area) < 1e-9


def test_fishnet_intersects_filter():
    # L 形范围：右上角挖掉 1 格
    geom = box(100.0, 30.0, 100.2, 30.1).union(box(100.0, 30.1, 100.1, 30.2))
    grids = fishnet(geom, 0.1)
    assert len(grids) == 3
    for g in grids:
        assert g.to_polygon().intersects(geom)


def test_fishnet_edge_clipping():
    # bbox 不整除 size：边缘网格被裁剪，仍全覆盖
    geom = box(100.0, 30.0, 100.25, 30.15)
    grids = fishnet(geom, 0.1)
    union = unary_union([g.to_polygon() for g in grids])
    assert abs(union.area - geom.area) < 1e-9


def test_fishnet_invalid_size():
    with pytest.raises(ValueError):
        fishnet(box(0, 0, 1, 1), 0)


def test_split_quadrants():
    g = Grid(gid="g0_0", xmin=100.0, ymin=30.0, xmax=100.2, ymax=30.2, depth=0)
    children = split(g)
    assert len(children) == 4
    assert [c.gid for c in children] == ["g0_0-0", "g0_0-1", "g0_0-2", "g0_0-3"]
    assert all(c.depth == 1 for c in children)
    # 四个子网格恰好铺满父网格
    union = unary_union([c.to_polygon() for c in children])
    assert abs(union.area - g.to_polygon().area) < 1e-12
    # 子网格边长减半
    assert abs(children[0].edge - g.edge / 2) < 1e-12


def test_polygon_str_format():
    """polygon_str 返回 GCJ-02 请求坐标：格式 6 位小数，且反解回 WGS-84 与网格角点一致。"""
    from amap_poi_fetcher.coords import gcj02_to_wgs84
    g = Grid(gid="g", xmin=121.123456789, ymin=30.0, xmax=122.0, ymax=31.987654321)
    s = g.polygon_str
    nw, se = s.split("|")
    nw_lng, nw_lat = (float(v) for v in nw.split(","))
    se_lng, se_lat = (float(v) for v in se.split(","))
    # 反解回 WGS-84，应还原网格角点（米级容差）
    lng_min, lat_max = gcj02_to_wgs84(nw_lng, nw_lat)
    lng_max, lat_min = gcj02_to_wgs84(se_lng, se_lat)
    assert abs(lng_min - g.xmin) < 1e-4
    assert abs(lat_max - g.ymax) < 1e-4
    assert abs(lng_max - g.xmax) < 1e-4
    assert abs(lat_min - g.ymin) < 1e-4
    # 6 位小数格式
    assert re.match(r"^-?\d+\.\d{6},-?\d+\.\d{6}\|-?\d+\.\d{6},-?\d+\.\d{6}$", s)


def test_polygon_str_out_of_china_unchanged():
    """境外网格（如纽约）无火星偏移，polygon_str 应保持 WGS-84 原值。"""
    g = Grid(gid="g", xmin=-74.0, ymin=40.7, xmax=-73.9, ymax=40.8)
    assert g.polygon_str == "-74.000000,40.800000|-73.900000,40.700000"
