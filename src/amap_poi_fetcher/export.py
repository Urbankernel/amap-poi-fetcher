"""最终导出：outputs/ 下的 CSV / GeoJSON / GPKG / SHP（均为 WGS-84）。

- 增量 CSV 本身已是最终格式，csv 导出即拷贝；
- GeoJSON 流式生成，属性保留全部字段（含 GCJ-02 原值与 grid_id 溯源信息）；
- GPKG（优先）：单文件三图层 pois（点）+ study_area（范围面）+ grids（渔网网格面）；
  SHP（兼容）：pois.shp + study_area.shp + grids.shp，
  字段名受 10 字符限制，仅 poi_type_code → poi_code；
- 渔网网格属性：gid（含四分象限后缀，可溯源）/ depth（递归深度）/ truncated（1=疑似截断）；
- GPKG/SHP 依赖可选包 geopandas（延迟导入），缺包时告警跳过，不影响其他产物。
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from .grid import Grid
from .store import FIELDNAMES, RunStore

logger = logging.getLogger("amap_poi_fetcher.export")

# GPKG/SHP 图层名
LAYER_POIS = "pois"
LAYER_STUDY_AREA = "study_area"
LAYER_GRIDS = "grids"

# SHP 字段名 ≤10 字符：仅 poi_type_code 超长，显式映射
SHP_COLUMN_MAP = {"poi_type_code": "poi_code"}

_CRS_WGS84 = "EPSG:4326"


def export_csv(store: RunStore) -> Path:
    """拷贝增量 CSV 到 outputs/pois.csv。"""
    out = store.outputs_dir / "pois.csv"
    store.close()  # 确保落盘完整后再拷贝
    shutil.copyfile(store.csv_path, out)
    logger.info("CSV 导出: %s", out)
    return out


def export_geojson(store: RunStore) -> Path:
    """增量 CSV → GeoJSON FeatureCollection（WGS-84 Point）。"""
    out = store.outputs_dir / "pois.geojson"
    n = 0
    with open(store.csv_path, newline="", encoding="utf-8-sig") as f, \
            open(out, "w", encoding="utf-8") as g:
        g.write('{"type":"FeatureCollection","features":[')
        first = True
        for row in csv.DictReader(f):
            try:
                lon, lat = float(row["lon"]), float(row["lat"])
            except (KeyError, ValueError):
                continue
            feature = {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {k: v for k, v in row.items() if k not in ("lon", "lat")},
            }
            if not first:
                g.write(",")
            json.dump(feature, g, ensure_ascii=False, separators=(",", ":"))
            first = False
            n += 1
        g.write("]}")
    logger.info("GeoJSON 导出: %s（%d 个要素）", out, n)
    return out


# ------------------------------------------------------------------ GPKG / SHP

def _require_geopandas():
    """延迟导入 geopandas；缺包时告警并返回 None（跳过矢量导出，不中断流程）。"""
    try:
        import geopandas as gpd
        return gpd
    except ImportError:
        logger.warning("导出 GPKG/SHP 需要可选依赖 geopandas，已跳过："
                       "pip install -r requirements-optional.txt")
        return None


def _load_pois_geodataframe(gpd, store: RunStore):
    """增量 CSV → GeoDataFrame（Point, EPSG:4326），属性保留除 lon/lat 外全部字段。"""
    attrs: list[dict[str, str]] = []
    lons: list[float] = []
    lats: list[float] = []
    with open(store.csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                lon, lat = float(row["lon"]), float(row["lat"])
            except (KeyError, ValueError):
                continue
            attrs.append({k: v for k, v in row.items() if k not in ("lon", "lat")})
            lons.append(lon)
            lats.append(lat)
    geometry = gpd.points_from_xy(lons, lats)
    return gpd.GeoDataFrame(attrs, geometry=geometry, crs=_CRS_WGS84)


def _study_area_geodataframe(gpd, scope_geom: BaseGeometry, scope_name: str):
    """研究范围几何 → 单要素 GeoDataFrame（Polygon/MultiPolygon, EPSG:4326）。"""
    return gpd.GeoDataFrame({"name": [scope_name]}, geometry=[scope_geom], crs=_CRS_WGS84)


def _grids_geodataframe(gpd, grids: list[Grid], truncated_ids: set[str]):
    """渔网网格 → GeoDataFrame（Polygon, EPSG:4326），属性 gid/depth/truncated。

    truncated 用 0/1 整型而非 bool，兼容 SHP dbf 与 GPKG 两种驱动。
    """
    data = [
        {"gid": g.gid, "depth": g.depth,
         "truncated": 1 if g.gid in truncated_ids else 0}
        for g in grids
    ]
    return gpd.GeoDataFrame(
        data, geometry=[g.to_polygon() for g in grids], crs=_CRS_WGS84)


def export_gpkg(
    store: RunStore,
    scope_geom: BaseGeometry,
    scope_name: str,
    grids: list[Grid] | None = None,
    truncated_ids: set[str] | None = None,
) -> Path | None:
    """导出 pois.gpkg：单文件图层 pois（点）+ study_area（面）+ grids（网格面）。

    grids 为 None 或空时跳过网格图层。老版本 geopandas/fiona 不支持向既有
    GPKG 追加图层时，study_area / grids 退化为独立 gpkg 文件。缺 geopandas 返回 None。
    """
    gpd = _require_geopandas()
    if gpd is None:
        return None
    store.close()
    out = store.outputs_dir / "pois.gpkg"
    if out.exists():
        out.unlink()  # 避免旧图层残留

    pois = _load_pois_geodataframe(gpd, store)
    pois.to_file(out, layer=LAYER_POIS, driver="GPKG")

    # 待追加的图层：(图层名, GeoDataFrame)
    extra_layers: list[tuple[str, object]] = [
        (LAYER_STUDY_AREA, _study_area_geodataframe(gpd, scope_geom, scope_name))]
    if grids:
        extra_layers.append((LAYER_GRIDS, _grids_geodataframe(gpd, grids, truncated_ids or set())))

    layer_names = [LAYER_POIS]
    try:
        for layer_name, gdf in extra_layers:
            gdf.to_file(out, layer=layer_name, driver="GPKG", mode="a")
            layer_names.append(layer_name)
        logger.info("GPKG 导出: %s（图层: %s；POI %d 个要素）",
                    out, ", ".join(layer_names), len(pois))
    except TypeError:
        for layer_name, gdf in extra_layers:
            fallback = store.outputs_dir / f"{layer_name}.gpkg"
            gdf.to_file(fallback, driver="GPKG")
            logger.info("当前 geopandas 不支持追加图层，%s 独立导出: %s", layer_name, fallback)
        logger.info("GPKG 导出: %s（图层: %s；POI %d 个要素）", out, LAYER_POIS, len(pois))
    return out


def export_shp(
    store: RunStore,
    scope_geom: BaseGeometry,
    scope_name: str,
    grids: list[Grid] | None = None,
    truncated_ids: set[str] | None = None,
) -> list[Path]:
    """导出 SHP（兼容格式）：pois.shp + study_area.shp + grids.shp（有网格时）。

    字段名映射 10 字符限制（poi_type_code → poi_code），UTF-8 编码（.cpg）。
    缺 geopandas 返回空列表。
    """
    gpd = _require_geopandas()
    if gpd is None:
        return []
    store.close()

    pois = _load_pois_geodataframe(gpd, store).rename(columns=SHP_COLUMN_MAP)
    pois_path = store.outputs_dir / "pois.shp"
    pois.to_file(pois_path, driver="ESRI Shapefile", encoding="utf-8")

    study = _study_area_geodataframe(gpd, scope_geom, scope_name)
    study_path = store.outputs_dir / "study_area.shp"
    study.to_file(study_path, driver="ESRI Shapefile", encoding="utf-8")

    paths = [pois_path, study_path]
    if grids:
        grids_gdf = _grids_geodataframe(gpd, grids, truncated_ids or set())
        grids_path = store.outputs_dir / "grids.shp"
        grids_gdf.to_file(grids_path, driver="ESRI Shapefile", encoding="utf-8")
        paths.append(grids_path)

    logger.info("SHP 导出: %s（POI %d 个要素）",
                ", ".join(p.name for p in paths), len(pois))
    return paths
