"""file 模式：导入本地矢量文件作为范围。

- .geojson/.json：标准库 json 原生解析（零依赖），坐标按 WGS-84 处理（RFC 7946）；
- .shp/.gpkg/.kml 等：走可选依赖 geopandas（延迟导入，缺包给出明确提示），
  任意坐标系自动归一 WGS-84；
- 多个面取并集，未闭合/自相交面自动修复（buffer(0)）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from . import ScopeResult

logger = logging.getLogger("amap_poi_fetcher.scope.fileio")

_GEOJSON_SUFFIXES = {".geojson", ".json"}


def _fix(geom: BaseGeometry) -> BaseGeometry:
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom


def _load_geojson_native(path: Path) -> BaseGeometry:
    """标准库解析 GeoJSON：支持 FeatureCollection / Feature / 裸 Geometry。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    obj_type = data.get("type")
    if obj_type == "FeatureCollection":
        geoms = [shape(feat["geometry"]) for feat in data.get("features", [])
                 if feat.get("geometry")]
        if not geoms:
            raise ValueError(f"{path.name} 中没有任何几何要素")
        return _fix(unary_union(geoms))
    if obj_type == "Feature":
        return _fix(shape(data["geometry"]))
    if obj_type in ("Point", "MultiPoint", "LineString", "MultiLineString",
                    "Polygon", "MultiPolygon", "GeometryCollection"):
        return _fix(shape(data))
    raise ValueError(f"无法识别的 GeoJSON 类型: {obj_type!r}")


def _load_with_geopandas(path: Path, layer: str | None) -> BaseGeometry:
    try:
        import geopandas as gpd
    except ImportError as e:
        raise ImportError(
            f"导入 {path.suffix} 文件需要 geopandas："
            "pip install -r requirements-optional.txt（GeoJSON 文件无需此依赖）"
        ) from e
    gdf = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    if gdf.empty:
        raise ValueError(f"{path.name} 中没有任何要素")
    if gdf.crs is None:
        logger.warning("%s 缺少坐标系定义，按 WGS-84 处理", path.name)
        gdf = gdf.set_crs(4326)
    gdf = gdf.to_crs(4326)
    return _fix(unary_union(list(gdf.geometry)))


def build(cfg: dict[str, Any]) -> ScopeResult:
    raw_path = cfg.get("path")
    if not raw_path:
        raise ValueError("file 模式需要 path（本地矢量文件路径）")
    path = Path(raw_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"范围文件不存在: {path}")

    if path.suffix.lower() in _GEOJSON_SUFFIXES:
        geom = _load_geojson_native(path)
    else:
        geom = _load_with_geopandas(path, cfg.get("layer"))
    logger.info("范围文件: %s（bounds=%s）", path.name, [round(v, 4) for v in geom.bounds])
    return ScopeResult(geometry=geom, name=path.stem)
