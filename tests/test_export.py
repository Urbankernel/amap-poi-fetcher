"""GPKG/SHP 导出测试（需要可选依赖 geopandas，缺包自动跳过）。

验证：
- GPKG 单文件双图层（pois 点 + study_area 面），字段全保留，CRS=EPSG:4326；
- SHP 双文件，字段名 10 字符映射（poi_type_code → poi_code）；
- 研究范围图层为单要素面。
"""

import pytest

gpd = pytest.importorskip("geopandas")

from shapely.geometry import box

from amap_poi_fetcher.export import (
    LAYER_GRIDS,
    LAYER_POIS,
    LAYER_STUDY_AREA,
    export_gpkg,
    export_shp,
)
from amap_poi_fetcher.grid import Grid
from amap_poi_fetcher.store import RunStore

TEST_GRIDS = [
    Grid("g0_0", 121.0, 31.05, 121.1, 31.1, depth=0),
    Grid("g0_1-2", 121.0, 31.0, 121.05, 31.05, depth=1),
]
TEST_TRUNCATED = {"g0_1-2"}


def make_store(tmp_path) -> RunStore:
    store = RunStore.create(tmp_path / "runs", "test", {"types": "050301"})
    store.append_pois([
        {"poi_id": "A1", "lon": 121.05, "lat": 31.05, "lon_gcj02": 121.055, "lat_gcj02": 31.056,
         "name": "门店甲", "poi_type": "餐饮服务;快餐店;肯德基", "poi_type_code": "050301",
         "cityname": "上海市", "adname": "浦东新区", "address": "世纪大道 1 号", "grid_id": "g0_0"},
        {"poi_id": "A2", "lon": 121.06, "lat": 31.06, "lon_gcj02": 121.065, "lat_gcj02": 31.066,
         "name": "门店乙", "poi_type": "餐饮服务;快餐店;麦当劳", "poi_type_code": "050302",
         "cityname": "上海市", "adname": "浦东新区", "address": "世纪大道 2 号", "grid_id": "g0_0"},
    ])
    return store


def test_gpkg_three_layers(tmp_path):
    store = make_store(tmp_path)
    geom = box(121.0, 31.0, 121.1, 31.1)
    out = export_gpkg(store, geom, "测试范围",
                      grids=TEST_GRIDS, truncated_ids=TEST_TRUNCATED)
    store.close()

    assert out is not None and out.exists()
    pois = gpd.read_file(out, layer=LAYER_POIS)
    study = gpd.read_file(out, layer=LAYER_STUDY_AREA)
    grids = gpd.read_file(out, layer=LAYER_GRIDS)

    assert len(pois) == 2
    assert "poi_type_code" in pois.columns          # GPKG 字段全保留
    assert set(pois["poi_id"]) == {"A1", "A2"}
    assert pois.crs.to_epsg() == 4326
    assert pois.geometry.geom_type.eq("Point").all()

    assert len(study) == 1
    assert study.iloc[0]["name"] == "测试范围"
    assert study.crs.to_epsg() == 4326
    assert study.iloc[0].geometry.equals(geom)

    # 网格图层：gid/depth/truncated 三属性 + 面几何
    assert len(grids) == 2
    assert set(grids["gid"]) == {"g0_0", "g0_1-2"}
    assert dict(zip(grids["gid"], grids["depth"])) == {"g0_0": 0, "g0_1-2": 1}
    assert dict(zip(grids["gid"], grids["truncated"])) == {"g0_0": 0, "g0_1-2": 1}
    assert grids.crs.to_epsg() == 4326
    assert grids.geometry.geom_type.eq("Polygon").all()


def test_shp_outputs(tmp_path):
    store = make_store(tmp_path)
    geom = box(121.0, 31.0, 121.1, 31.1)
    paths = export_shp(store, geom, "测试范围",
                       grids=TEST_GRIDS, truncated_ids=TEST_TRUNCATED)
    store.close()

    assert len(paths) == 3
    pois_path, study_path, grids_path = paths
    assert pois_path.name == "pois.shp" and study_path.name == "study_area.shp"
    assert grids_path.name == "grids.shp"
    assert pois_path.exists() and study_path.exists()
    # SHP 附属文件齐全（.shx/.dbf/.prj/.cpg）
    for suffix in (".shx", ".dbf", ".prj", ".cpg"):
        assert pois_path.with_suffix(suffix).exists(), f"缺 {suffix}"

    pois = gpd.read_file(pois_path)
    assert "poi_code" in pois.columns               # 10 字符映射生效
    assert "poi_type_code" not in pois.columns
    assert len(pois) == 2
    assert pois.crs.to_epsg() == 4326

    study = gpd.read_file(study_path)
    assert len(study) == 1
    assert study.iloc[0].geometry.equals(geom)

    grids = gpd.read_file(grids_path)
    assert len(grids) == 2
    assert set(grids.columns) >= {"gid", "depth", "truncated"}  # 均 ≤10 字符无需映射
    assert dict(zip(grids["gid"], grids["truncated"])) == {"g0_0": 0, "g0_1-2": 1}
